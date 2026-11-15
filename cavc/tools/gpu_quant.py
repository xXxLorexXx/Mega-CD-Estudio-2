"""Optional CuPy acceleration for palette assignment and index generation.

Quantization is frame-independent and accounts for much of sim.py's runtime.
This module moves the squared-error palette assignment and nearest-colour index
work to the GPU. Its output is bit-identical to the CPU implementation.

GPU use defaults on and can be disabled explicitly with
``CBRSIM_GPU=0/off/false/no``. Missing CuPy or CUDA falls back to the CPU. The
CUDA runtime lives in the repository's uv-managed ``.venv-gpu`` and does not
inherit system Python, NumPy, or Pillow. Start GPU sims with
``tools/python.sh --gpu tools/sim.py ...``.
"""
import os
import numpy as np

_STATE = {"checked": False, "on": False, "cp": None}


def enabled():
    """Return whether the default-on GPU path is available, checking once."""
    if _STATE["checked"]:
        return _STATE["on"]
    _STATE["checked"] = True
    if os.environ.get("CBRSIM_GPU", "1").strip().lower() in ("0", "off", "false", "no"):
        print("[gpu_quant] CUDA desactivado. Se usa la CPU")
        return False
    try:
        import cupy as cp
        # デバイスに一度触れて JIT カーネルまで通ることを確認
        a = cp.arange(4, dtype=cp.int32).reshape(1, 1, 1, 1, 4)
        b = cp.arange(4, dtype=cp.int32).reshape(1, 1, 1, 1, 4)
        int(((a - b) ** 2).sum())
        cp.cuda.Stream.null.synchronize()
        _STATE["cp"] = cp
        _STATE["on"] = True
        name = cp.cuda.runtime.getDeviceProperties(0)["name"].decode()
        print(f"[gpu_quant] CUDA activo: {name}")
    except Exception as e:  # noqa: BLE001 (どんな失敗でもCPUへ退避)
        print(f"[gpu_quant] CUDA no disponible. Se usa la CPU: {e}")
        _STATE["on"] = False
    return _STATE["on"]


def disable():
    _STATE.update(checked=True, on=False)


def cupy():
    """Return the initialized CuPy module for shared palette GPU evaluators."""
    if not enabled():
        raise RuntimeError("CuPy/CUDA is not available")
    return _STATE["cp"]


_QUANT_KERNELS_SRC = r"""
extern "C" __global__
void quant_keys_and_perr(
    const unsigned char* __restrict__ flat,   // (BC, 64, 3)
    const short* __restrict__ error,          // (L, 512)
    unsigned short* __restrict__ keys,        // (BC, 64)
    int* __restrict__ perr,                   // (BC, L)
    signed char* __restrict__ assign,         // (BC,)
    int BC, int L)
{
    int bc = blockIdx.x * blockDim.x + threadIdx.x;
    if (bc >= BC) return;
    const unsigned char* f = flat + (size_t)bc * 192;
    unsigned short* k_out = keys + (size_t)bc * 64;
    int sums[8];
    for (int l = 0; l < L; ++l) sums[l] = 0;
    for (int p = 0; p < 64; ++p) {
        int r = f[p * 3 + 0];
        int g = f[p * 3 + 1];
        int b = f[p * 3 + 2];
        unsigned short key = (unsigned short)((r << 6) | (g << 3) | b);
        k_out[p] = key;
        for (int l = 0; l < L; ++l) {
            sums[l] += (int)error[(size_t)l * 512 + key];
        }
    }
    int best_l = 0;
    int best_val = sums[0];
    int* p_out = perr + (size_t)bc * L;
    p_out[0] = best_val;
    for (int l = 1; l < L; ++l) {
        int val = sums[l];
        p_out[l] = val;
        if (val < best_val) {
            best_val = val;
            best_l = l;
        }
    }
    assign[bc] = (signed char)best_l;
}

extern "C" __global__
void coherent_checker_step(
    const unsigned short* __restrict__ keys,  // (B, rows*cols, 64)
    const int* __restrict__ perr,             // (B, rows*cols, L)
    const signed char* __restrict__ res_lut,  // (L, 512, 3)
    const signed char* __restrict__ cur_in,   // (B, rows*cols)
    signed char* __restrict__ cur_out,        // (B, rows*cols)
    int B, int rows, int cols, int L,
    double seam_weight, int parity)
{
    int col = blockIdx.x * blockDim.x + threadIdx.x;
    int row = blockIdx.y * blockDim.y + threadIdx.y;
    int b = blockIdx.z;
    if (b >= B || row >= rows || col >= cols) return;
    int C = rows * cols;
    int rc = row * cols + col;
    if (((row + col) & 1) != parity) {
        cur_out[(size_t)b * C + rc] = cur_in[(size_t)b * C + rc];
        return;
    }
    const size_t base = (size_t)b * C;
    const unsigned short* my_k = keys + (base + rc) * 64;
    const int* my_perr = perr + (base + rc) * L;

    int la = (row > 0) ? (int)cur_in[base + (row - 1) * cols + col] : -1;
    int lb = (row + 1 < rows) ? (int)cur_in[base + (row + 1) * cols + col] : -1;
    int lc = (col > 0) ? (int)cur_in[base + row * cols + (col - 1)] : -1;
    int ld = (col + 1 < cols) ? (int)cur_in[base + row * cols + (col + 1)] : -1;

    const unsigned short* ka = (la >= 0) ? keys + (base + (row - 1) * cols + col) * 64 : 0;
    const unsigned short* kb = (lb >= 0) ? keys + (base + (row + 1) * cols + col) * 64 : 0;
    const unsigned short* kc = (lc >= 0) ? keys + (base + row * cols + (col - 1)) * 64 : 0;
    const unsigned short* kd = (ld >= 0) ? keys + (base + row * cols + (col + 1)) * 64 : 0;

    int best_l = 0;
    double best_e = 1e300;
    for (int l = 0; l < L; ++l) {
        double e = (double)my_perr[l];
        const signed char* lut_l = res_lut + (size_t)l * 512 * 3;
        if (la >= 0) {
            const signed char* lut_n = res_lut + (size_t)la * 512 * 3;
            int s = 0;
            for (int x = 0; x < 8; ++x) {
                int k0 = my_k[x];
                int k1 = ka[56 + x];
                for (int ch = 0; ch < 3; ++ch) {
                    int d = (int)lut_l[k0 * 3 + ch] - (int)lut_n[k1 * 3 + ch];
                    s += d * d;
                }
            }
            e += seam_weight * (double)s;
        }
        if (lb >= 0) {
            const signed char* lut_n = res_lut + (size_t)lb * 512 * 3;
            int s = 0;
            for (int x = 0; x < 8; ++x) {
                int k0 = my_k[56 + x];
                int k1 = kb[x];
                for (int ch = 0; ch < 3; ++ch) {
                    int d = (int)lut_l[k0 * 3 + ch] - (int)lut_n[k1 * 3 + ch];
                    s += d * d;
                }
            }
            e += seam_weight * (double)s;
        }
        if (lc >= 0) {
            const signed char* lut_n = res_lut + (size_t)lc * 512 * 3;
            int s = 0;
            for (int y = 0; y < 8; ++y) {
                int k0 = my_k[y * 8];
                int k1 = kc[y * 8 + 7];
                for (int ch = 0; ch < 3; ++ch) {
                    int d = (int)lut_l[k0 * 3 + ch] - (int)lut_n[k1 * 3 + ch];
                    s += d * d;
                }
            }
            e += seam_weight * (double)s;
        }
        if (ld >= 0) {
            const signed char* lut_n = res_lut + (size_t)ld * 512 * 3;
            int s = 0;
            for (int y = 0; y < 8; ++y) {
                int k0 = my_k[y * 8 + 7];
                int k1 = kd[y * 8];
                for (int ch = 0; ch < 3; ++ch) {
                    int d = (int)lut_l[k0 * 3 + ch] - (int)lut_n[k1 * 3 + ch];
                    s += d * d;
                }
            }
            e += seam_weight * (double)s;
        }
        if (e < best_e) {
            best_e = e;
            best_l = l;
        }
    }
    cur_out[base + rc] = (signed char)best_l;
}

extern "C" __global__
void lookup_pidx(
    const unsigned short* __restrict__ keys,  // (BC, 64)
    const signed char* __restrict__ assign,   // (BC,)
    const unsigned char* __restrict__ index,  // (L, 512)
    unsigned char* __restrict__ pidx,         // (BC, 64)
    int BC)
{
    int bc = blockIdx.x * blockDim.x + threadIdx.x;
    if (bc >= BC) return;
    int l = (int)assign[bc];
    const unsigned short* k_in = keys + (size_t)bc * 64;
    const unsigned char* idx_l = index + (size_t)l * 512;
    unsigned char* out = pidx + (size_t)bc * 64;
    for (int p = 0; p < 64; ++p) {
        out[p] = (unsigned char)(idx_l[k_in[p]] + 1);
    }
}
"""
_QUANT_KERNELS = None


def _get_quant_kernels(cp):
    global _QUANT_KERNELS
    if _QUANT_KERNELS is None:
        mod = cp.RawModule(code=_QUANT_KERNELS_SRC)
        _QUANT_KERNELS = (
            mod.get_function("quant_keys_and_perr"),
            mod.get_function("coherent_checker_step"),
            mod.get_function("lookup_pidx"),
        )
    return _QUANT_KERNELS


class PalCache:
    """区間パレットのRGB333二乗誤差/index LUTをGPU上にキャッシュする。"""

    def __init__(self):
        self._d = {}
        self._res = {}
        self.cpu_only = False
        self.batch_limit = 64

    def get(self, cp, seg, seg_pals, exclude_deep_black=False):
        cache_key = (seg, bool(exclude_deep_black))
        g = self._d.get(cache_key)
        if g is None:
            host = np.asarray(seg_pals[seg], dtype=np.int16)                    # (4,15,3)
            pals = cp.asarray(host)
            key = cp.arange(512, dtype=cp.int16)
            rgb = cp.stack(((key >> 6) & 7, (key >> 3) & 7, key & 7), axis=1)
            distance = ((rgb[None, :, None, :] - pals[:, None, :, :]) ** 2).sum(3)
            if exclude_deep_black:
                # Mirror palette_lut: deep-black entries leave the candidate
                # set unless a line consists only of them.
                from quantize_global4_tiles import deep_black_entries
                mask = np.stack([deep_black_entries(line) for line in host])
                mask &= ~mask.all(axis=1, keepdims=True)
                distance = cp.where(
                    cp.asarray(mask)[:, None, :], cp.int32(32000),
                    distance.astype(cp.int32))
            index = distance.argmin(2).astype(cp.uint8)                        # (4,512)
            error = cp.take_along_axis(distance, index[:, :, None], axis=2)[:, :, 0]
            g = error.astype(cp.int16), index, pals
            self._d[cache_key] = g
        return g

    def get_with_residuals(self, cp, seg, seg_pals, exclude_deep_black=False):
        cache_key = (seg, bool(exclude_deep_black))
        entry = self._res.get(cache_key)
        if entry is None:
            error, index, pals = self.get(
                cp, seg, seg_pals, exclude_deep_black=exclude_deep_black)
            key = cp.arange(512, dtype=cp.int16)
            rgb = cp.stack(((key >> 6) & 7, (key >> 3) & 7, key & 7), axis=1)
            quant = cp.stack([pals[line][index[line]] for line in range(len(pals))])
            res_lut = (quant - rgb[None]).astype(cp.int8)
            entry = (error, index, pals, res_lut)
            self._res[cache_key] = entry
        return entry


def _coherent_assignment(cp, perr, keys, index, pals, rows, cols,
                         seam_weight, iterations):
    """Vectorized checkerboard equivalent of coherent_assign_idx()."""
    lines = len(pals)
    assignment = perr.argmin(1).reshape(rows, cols)
    quantized = cp.stack([
        pals[line][index[line, keys]]
        for line in range(lines)
    ]).astype(cp.int16)
    source = cp.stack((
        (keys >> 6) & 7,
        (keys >> 3) & 7,
        keys & 7,
    ), axis=-1).astype(cp.int16)
    residual = (quantized - source[None]).reshape(
        lines, rows, cols, 8, 8, 3)
    base_energy = perr.reshape(rows, cols, lines).astype(cp.float64)
    grid_row = cp.arange(rows)[:, None]
    grid_col = cp.arange(cols)[None, :]
    checkerboard = (grid_row + grid_col) & 1

    for _iteration in range(max(1, int(iterations))):
        for parity in (0, 1):
            energy = base_energy.copy()
            if rows > 1:
                rr = cp.arange(rows - 1)[:, None]
                cc = cp.arange(cols)[None, :]
                top = residual[assignment[:-1], rr, cc, 7]
                delta = residual[:, 1:, :, 0] - top[None]
                energy[1:] += seam_weight * (delta * delta).sum((-1, -2)).transpose(1, 2, 0)
                bottom = residual[assignment[1:], rr + 1, cc, 0]
                delta = residual[:, :-1, :, 7] - bottom[None]
                energy[:-1] += seam_weight * (delta * delta).sum((-1, -2)).transpose(1, 2, 0)
            if cols > 1:
                rr = cp.arange(rows)[:, None]
                cc = cp.arange(cols - 1)[None, :]
                left = residual[assignment[:, :-1], rr, cc, :, 7]
                delta = residual[:, :, 1:, :, 0] - left[None]
                energy[:, 1:] += seam_weight * (delta * delta).sum((-1, -2)).transpose(1, 2, 0)
                right = residual[assignment[:, 1:], rr, cc + 1, :, 0]
                delta = residual[:, :, :-1, :, 7] - right[None]
                energy[:, :-1] += seam_weight * (delta * delta).sum((-1, -2)).transpose(1, 2, 0)
            candidate = energy.argmin(2)
            assignment = cp.where(checkerboard == parity, candidate, assignment)
    return assignment.reshape(-1)


def _assign_idx_batch_cuda(flats, seg, seg_pals, cache, coherent_shape=None,
                     seam_weight=0.0, seam_iterations=2,
                     exclude_deep_black=False):
    """Batched GPU palette assignment for ``B`` frames sharing one segment."""
    cp = _STATE["cp"]
    error, index, pals, res_lut = cache.get_with_residuals(
        cp, seg, seg_pals, exclude_deep_black=exclude_deep_black)
    flats_arr = np.ascontiguousarray(flats, dtype=np.uint8)
    if flats_arr.ndim != 4 or flats_arr.shape[2:] != (64, 3):
        raise ValueError(f"expected (B,C,64,3), got {flats_arr.shape}")
    B, C, _, _ = flats_arr.shape
    BC = B * C
    L = len(pals)
    if BC == 0:
        return np.empty((B, C), np.int8), np.empty((B, C, 64), np.uint8)
    k_perr, k_step, k_lookup = _get_quant_kernels(cp)
    d_flat = cp.asarray(flats_arr)
    d_keys = cp.empty((B, C, 64), dtype=cp.uint16)
    d_perr = cp.empty((B, C, L), dtype=cp.int32)
    d_assign = cp.empty((B, C), dtype=cp.int8)
    threads = 256
    blocks = (BC + threads - 1) // threads
    k_perr((blocks,), (threads,), (d_flat, error, d_keys, d_perr, d_assign, np.int32(BC), np.int32(L)))
    if coherent_shape is not None and seam_weight > 0 and L > 1:
        rows, cols = map(int, coherent_shape)
        if C != rows * cols:
            raise ValueError(f"tile count {C} differs from {rows}x{cols}")
        d_buf = cp.empty_like(d_assign)
        step_block = (16, 16, 1)
        step_grid = ((cols + 15) // 16, (rows + 15) // 16, B)
        weight = np.float64(seam_weight)
        for _ in range(max(1, int(seam_iterations))):
            for parity in (0, 1):
                k_step(step_grid, step_block, (
                    d_keys, d_perr, res_lut, d_assign, d_buf,
                    np.int32(B), np.int32(rows), np.int32(cols), np.int32(L),
                    weight, np.int32(parity),
                ))
                d_assign, d_buf = d_buf, d_assign
    d_pidx = cp.empty((B, C, 64), dtype=cp.uint8)
    k_lookup((blocks,), (threads,), (d_keys, d_assign, index, d_pidx, np.int32(BC)))
    return cp.asnumpy(d_assign).astype(np.int8, copy=False), cp.asnumpy(d_pidx).astype(np.uint8, copy=False)


def is_cuda_error(error):
    cp = _STATE["cp"]
    return (cp is not None and isinstance(error, (
        cp.cuda.runtime.CUDARuntimeError, cp.cuda.memory.OutOfMemoryError,
        cp.cuda.compiler.CompileException, cp.cuda.driver.CUDADriverError,
    )))


def assign_idx_batch(flats, seg, seg_pals, cache, coherent_shape=None,
                     seam_weight=0.0, seam_iterations=2, exclude_deep_black=False):
    cp = _STATE["cp"]
    options = (seg, seg_pals, cache, coherent_shape, seam_weight,
               seam_iterations, exclude_deep_black)
    if not len(flats):
        return _assign_idx_batch_cuda(flats, *options)
    if not cache.cpu_only:
        limit = cache.batch_limit
        if len(flats) > limit:
            results = [assign_idx_batch(flats[i:i + limit], *options)
                       for i in range(0, len(flats), limit)]
            return tuple(np.concatenate(values) for values in zip(*results))
        try:
            return _assign_idx_batch_cuda(flats, *options)
        except Exception as error:
            if not is_cuda_error(error):
                raise
            out_of_memory = isinstance(error, cp.cuda.memory.OutOfMemoryError)
            if not out_of_memory:
                print(f"CUDA falló en cuantización. Se continúa por CPU: {error}", flush=True)
                cache.cpu_only = True
        if out_of_memory and len(flats) > 1:
            cp.get_default_memory_pool().free_all_blocks()
            cache.batch_limit = len(flats) // 2
            return assign_idx_batch(flats, *options)
        if out_of_memory:
            print("Memoria CUDA insuficiente. Se continúa por CPU.", flush=True)
            cache.cpu_only = True

    from palette_algorithms import coherent_assign_idx
    from quantize_global4_tiles import palette_lut
    palettes = seg_pals[seg]
    if coherent_shape is not None and seam_weight > 0 and len(palettes) > 1:
        results = [coherent_assign_idx(
            frame, palettes, *coherent_shape, seam_weight=seam_weight,
            iterations=seam_iterations, exclude_deep_black=exclude_deep_black)
            for frame in flats]
        assignments, indices = zip(*results)
        return np.asarray(assignments, np.int8), np.asarray(indices, np.uint8)
    tables = [palette_lut(line, squared=True, exclude_deep_black=exclude_deep_black)
              for line in palettes]
    error, index = (np.stack(values) for values in zip(*tables))
    keys = ((flats[..., 0].astype(np.uint16) << 6)
            | (flats[..., 1].astype(np.uint16) << 3) | flats[..., 2])
    assignment = error[:, keys].sum(axis=-1).argmin(axis=0)
    return assignment.astype(np.int8), (index[assignment[..., None], keys] + 1).astype(np.uint8)


def assign_idx_one(flat, seg, seg_pals, cache, coherent_shape=None,
                   seam_weight=0.0, seam_iterations=2,
                   exclude_deep_black=False):
    """1コマ分。flat (C,64,3) uint8 rgb333 -> (assign int8 (C,), pidx uint8 (C,64) 1..15)。

    CPU版 assign_palette/idx_for と同一結果(argmin の最初の最小=同じtie挙動)。
    ``exclude_deep_black`` は palette_lut と同じく、深い黒entryを候補から外す。
    """
    a, p = assign_idx_batch(
        np.asarray(flat, dtype=np.uint8)[None], seg, seg_pals, cache,
        coherent_shape=coherent_shape, seam_weight=seam_weight,
        seam_iterations=seam_iterations, exclude_deep_black=exclude_deep_black,
    )
    return a[0], p[0]


def _tile_err(cp, keys, pal, T):
    """RGB333 LUTでSTL4のL1タイル誤差をGPU計算する。CPU版と同一。"""
    P = cp.asarray(np.asarray(pal, dtype=np.int16))          # (15,3)
    key = cp.arange(512, dtype=cp.int16)
    rgb = cp.stack(((key >> 6) & 7, (key >> 3) & 7, key & 7), axis=1)
    lut = cp.abs(rgb[:, None, :] - P[None, :, :]).sum(2).min(1)
    return cp.asnumpy(lut[keys].reshape(T, 64).sum(1))       # (T,)


def build_palettes(train_tiles, palette15, n_pal=4, iters=6, edge_w=None):
    """CPU版 build_palettes(quantize_global4_tiles) の GPU 版。重い tile_errors だけ GPU、
    palette15(k-means, 高速)は CPU のまま=出力はCPU版とビット一致。tiles は一度だけGPUへ。
    edge_w(T,64)=画素ごとのエッジ学習重み(CBRSIM_EDGE_WEIGHT)。palette15 に渡す。"""
    cp = _STATE["cp"]
    T = train_tiles.shape[0]

    def _w(mask):
        return None if edge_w is None else edge_w[mask].reshape(-1)
    means = train_tiles.reshape(T, 64, 3).mean(1)
    order = np.argsort(means.sum(1))
    groups = np.array_split(order, n_pal)
    pals = [palette15(train_tiles[g].reshape(-1, 3), weights=_w(g)) for g in groups]
    flat = np.asarray(train_tiles, dtype=np.uint16).reshape(-1, 3)
    keys = cp.asarray((flat[:, 0] << 6) | (flat[:, 1] << 3) | flat[:, 2])
    for _ in range(iters):
        err = np.stack([_tile_err(cp, keys, pl, T) for pl in pals], axis=1)  # (T,n_pal)
        assign = err.argmin(1)
        pals = [pals[p] if not (assign == p).any()
                else palette15(train_tiles[assign == p].reshape(-1, 3), weights=_w(assign == p))
                for p in range(n_pal)]
    del keys
    return pals
