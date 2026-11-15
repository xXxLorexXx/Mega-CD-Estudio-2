"""Optional compiled loops used by the CAVC encoder."""
import numpy as np

try:
    from numba import njit
    HAS_NUMBA = True
except ImportError:
    HAS_NUMBA = False
    def njit(**_options):
        return lambda function: function


@njit(cache=True)
def peak_buffer_draw(demand, supply):
    draw = 0
    peak = 0
    for i in range(len(demand)):
        draw = max(0, draw + int(demand[i]) - int(supply[i]))
        peak = max(peak, draw)
    return peak


@njit(cache=True, inline="always")
def _pairwise_tile_metrics(table, pixels, target, lanes, track_peak):
    # Match NumPy's pairwise reduction on a contiguous 64-pixel tile.
    peak = 0.0
    for lane in range(8):
        lanes[lane] = table[pixels[lane], target[lane]]
        if track_peak:
            peak = max(peak, lanes[lane])
    for i in range(8, 64, 8):
        for lane in range(8):
            value = table[pixels[i + lane], target[i + lane]]
            lanes[lane] += value
            if track_peak:
                peak = max(peak, value)
    left = (lanes[0] + lanes[1]) + (lanes[2] + lanes[3])
    right = (lanes[4] + lanes[5]) + (lanes[6] + lanes[7])
    return (left + right) / 64, peak


@njit(cache=True)
def best_resident_metrics(candidates, target, luma, chroma):
    best = -1
    score = float("inf")
    best_mean = best_peak = best_chroma = 0.0
    lanes = np.empty(8, dtype=np.float64)
    for index in range(len(candidates)):
        pixels = candidates[index]
        mean, peak = _pairwise_tile_metrics(luma, pixels, target, lanes, True)
        lower_bound = mean + 0.3 * peak
        # Chroma error is non-negative, so this candidate cannot improve the
        # current winner if its luma score already reaches the full score.
        if lower_bound >= score:
            continue
        color, _ = _pairwise_tile_metrics(chroma, pixels, target, lanes, False)
        distance = lower_bound + 0.5 * color
        if distance < score:
            score = distance
            best, best_mean, best_peak, best_chroma = index, mean, peak, color
    return best, best_mean, best_peak, best_chroma


@njit(cache=True)
def find_clock_slot(refs, previous, reused, pinned, mandatory,
                    hand, frame, mode):
    """Return the first legal clock candidate without Python scalar indexing."""
    for offset in range(len(refs)):
        slot = (hand + offset) % len(refs)
        if refs[slot] != 0:
            continue
        protected = mandatory[slot] and pinned[slot] >= frame
        if mode == 3:
            if not protected:
                return slot
            continue
        if reused is not None and reused[slot]:
            continue
        if mode == 2:
            if protected:
                return slot
            continue
        if previous[slot]:
            continue
        if mode == 1:
            if pinned[slot] >= frame and not mandatory[slot]:
                return slot
        elif not protected:
            return slot
    return -1


@njit(cache=True)
def f3_mask_eval_numba(cur, plain, changed, lw, cb, cr, ym_t, yp_t, c_t):
    """Evaluate F3 thresholds strictly on changed cells without float64 temporaries."""
    n_cells = len(changed)
    out = np.zeros(n_cells, dtype=np.bool_)
    lw0, lw1, lw2 = lw[0], lw[1], lw[2]
    cb0, cb1, cb2 = cb[0], cb[1], cb[2]
    cr0, cr1, cr2 = cr[0], cr[1], cr[2]
    for c in range(n_cells):
        if not changed[c]:
            continue
        sum_dy = 0.0
        max_dy = 0.0
        sum_dc = 0.0
        for y in range(8):
            for x in range(8):
                or_ = float(cur[c, y, x, 0])
                og = float(cur[c, y, x, 1])
                ob = float(cur[c, y, x, 2])
                pr = float(plain[c, y, x, 0])
                pg = float(plain[c, y, x, 1])
                pb = float(plain[c, y, x, 2])
                dy = abs((or_ * lw0 + og * lw1 + ob * lw2) - (pr * lw0 + pg * lw1 + pb * lw2))
                sum_dy += dy
                if dy > max_dy:
                    max_dy = dy
                dcb = (or_ * cb0 + og * cb1 + ob * cb2) - (pr * cb0 + pg * cb1 + pb * cb2)
                dcr = (or_ * cr0 + og * cr1 + ob * cr2) - (pr * cr0 + pg * cr1 + pb * cr2)
                sum_dc += np.sqrt(dcb * dcb + dcr * dcr)
        if (sum_dy / 64.0) <= ym_t and max_dy <= yp_t and (sum_dc / 64.0) <= c_t:
            out[c] = True
    return out


@njit(cache=True)
def tile_edge_error_numba(displayed, target, trows, tcols):
    """Compute tile boundary residual disagreement without 5D int16 allocations."""
    score = np.zeros(trows * tcols, dtype=np.float64)
    # Horizontal shared edges: between (r, c - 1) col 7 and (r, c) col 0
    for r in range(trows):
        row_base = r * tcols
        for c in range(1, tcols):
            idx_right = row_base + c
            idx_left = idx_right - 1
            h_sum = np.int32(0)
            for y in range(8):
                for ch in range(3):
                    res_r = np.int32(displayed[idx_right, y, 0, ch]) - np.int32(target[idx_right, y, 0, ch])
                    res_l = np.int32(displayed[idx_left, y, 7, ch]) - np.int32(target[idx_left, y, 7, ch])
                    diff = res_r - res_l
                    h_sum += -diff if diff < 0 else diff
            val = float(h_sum)
            score[idx_right] += val
            score[idx_left] += val
    # Vertical shared edges: between (r - 1, c) row 7 and (r, c) row 0
    for r in range(1, trows):
        row_base = r * tcols
        prev_base = (r - 1) * tcols
        for c in range(tcols):
            idx_bot = row_base + c
            idx_top = prev_base + c
            v_sum = np.int32(0)
            for x in range(8):
                for ch in range(3):
                    res_b = np.int32(displayed[idx_bot, 0, x, ch]) - np.int32(target[idx_bot, 0, x, ch])
                    res_t = np.int32(displayed[idx_top, 7, x, ch]) - np.int32(target[idx_top, 7, x, ch])
                    diff = res_b - res_t
                    v_sum += -diff if diff < 0 else diff
            val = float(v_sum)
            score[idx_bot] += val
            score[idx_top] += val
    return score


@njit(cache=True)
def cell_f3_score_lut(cur_flat, target, luma, chroma):
    """Compute F3 composite score (ym + 0.3*yp + 0.5*cm) for one 64-pixel cell via LUT."""
    lanes = np.empty(8, dtype=np.float64)
    ym, yp = _pairwise_tile_metrics(luma, cur_flat, target, lanes, True)
    cm, _ = _pairwise_tile_metrics(chroma, cur_flat, target, lanes, False)
    return ym + 0.3 * yp + 0.5 * cm


@njit(cache=True)
def score_smooth_fallback_numba(
    patterns, target, cur_colors, plain_colors, c, trows, tcols, f3_dy, f3_dc, md_lum
):
    """Compute (fidelity, quality) arrays for choose_smooth_fallback."""
    n_pat = patterns.shape[0]
    fidelity = np.empty(n_pat, dtype=np.float64)
    quality = np.empty(n_pat, dtype=np.float64)
    lanes = np.empty(8, dtype=np.float64)

    target_luma = np.empty((8, 8), dtype=np.float64)
    for idx in range(64):
        target_luma[idx >> 3, idx & 7] = md_lum[target[idx]]

    tgt_sub_mean = np.empty((2, 2), dtype=np.float64)
    tgt_sub_std = np.empty((2, 2), dtype=np.float64)
    for qy in range(2):
        for qx in range(2):
            s = 0.0
            for dy in range(4):
                for dx in range(4):
                    s += target_luma[qy * 4 + dy, qx * 4 + dx]
            m = s / 16.0
            tgt_sub_mean[qy, qx] = m
            v = 0.0
            for dy in range(4):
                for dx in range(4):
                    d = target_luma[qy * 4 + dy, qx * 4 + dx] - m
                    v += d * d
            tgt_sub_std[qy, qx] = np.sqrt(v / 16.0)

    row = c // tcols
    col = c - row * tcols
    has_left = col > 0
    has_right = col + 1 < tcols
    has_top = row > 0
    has_bot = row + 1 < trows
    edges = int(has_left) + int(has_right) + int(has_top) + int(has_bot)
    edge_div = float(edges if edges > 0 else 1)

    left_diff = np.empty(8, dtype=np.float64)
    right_diff = np.empty(8, dtype=np.float64)
    top_diff = np.empty(8, dtype=np.float64)
    bot_diff = np.empty(8, dtype=np.float64)

    if has_left:
        nb = c - 1
        for k in range(8):
            left_diff[k] = md_lum[cur_colors[nb, k, 7]] - md_lum[plain_colors[nb, k * 8 + 7]]
    if has_right:
        nb = c + 1
        for k in range(8):
            right_diff[k] = md_lum[cur_colors[nb, k, 0]] - md_lum[plain_colors[nb, k * 8]]
    if has_top:
        nb = c - tcols
        for k in range(8):
            top_diff[k] = md_lum[cur_colors[nb, 7, k]] - md_lum[plain_colors[nb, 56 + k]]
    if has_bot:
        nb = c + tcols
        for k in range(8):
            bot_diff[k] = md_lum[cur_colors[nb, 0, k]] - md_lum[plain_colors[nb, k]]

    luma = np.empty((8, 8), dtype=np.float64)
    residual = np.empty((8, 8), dtype=np.float64)

    for p in range(n_pat):
        pat = patterns[p]
        ym, yp = _pairwise_tile_metrics(f3_dy, pat, target, lanes, True)
        cm, _ = _pairwise_tile_metrics(f3_dc, pat, target, lanes, False)
        fid = ym + 0.3 * yp + 0.5 * cm
        fidelity[p] = fid

        for idx in range(64):
            y = idx >> 3
            x = idx & 7
            lum_val = md_lum[pat[idx]]
            luma[y, x] = lum_val
            residual[y, x] = lum_val - target_luma[y, x]

        shape_err = 0.0
        tex_err = 0.0
        for qy in range(2):
            for qx in range(2):
                s = 0.0
                for dy in range(4):
                    for dx in range(4):
                        s += luma[qy * 4 + dy, qx * 4 + dx]
                m = s / 16.0
                shape_err += abs(m - tgt_sub_mean[qy, qx])
                v = 0.0
                for dy in range(4):
                    for dx in range(4):
                        d = luma[qy * 4 + dy, qx * 4 + dx] - m
                        v += d * d
                std_val = np.sqrt(v / 16.0)
                ex = std_val - tgt_sub_std[qy, qx]
                if ex > 0.0:
                    tex_err += ex
        shape_err *= 0.25
        tex_err *= 0.25

        seam_err = 0.0
        if has_left:
            s = 0.0
            for k in range(8):
                s += abs(residual[k, 0] - left_diff[k])
            seam_err += s * 0.125
        if has_right:
            s = 0.0
            for k in range(8):
                s += abs(residual[k, 7] - right_diff[k])
            seam_err += s * 0.125
        if has_top:
            s = 0.0
            for k in range(8):
                s += abs(residual[0, k] - top_diff[k])
            seam_err += s * 0.125
        if has_bot:
            s = 0.0
            for k in range(8):
                s += abs(residual[7, k] - bot_diff[k])
            seam_err += s * 0.125

        quality[p] = fid + shape_err + tex_err + 1.5 * seam_err / edge_div

    return fidelity, quality


@njit(cache=True)
def score_match_motion_numba(
    colors, target, cur_colors, plain_colors, c, trows, tcols, detail_motion, f3_dy, f3_dc, md_lum
):
    """Compute candidate scores for match_motion."""
    n_cand = colors.shape[0]
    scores = np.empty(n_cand, dtype=np.float64)
    lanes = np.empty(8, dtype=np.float64)

    target_lum = np.empty(64, dtype=np.float64)
    for idx in range(64):
        target_lum[idx] = md_lum[target[idx]]

    row = c // tcols
    col = c - row * tcols
    has_left = col > 0
    has_right = col + 1 < tcols
    has_top = row > 0
    has_bot = row + 1 < trows
    edges = int(has_left) + int(has_right) + int(has_top) + int(has_bot)
    edge_div = float(edges if edges > 0 else 1)

    left_diff = np.empty(8, dtype=np.float64)
    right_diff = np.empty(8, dtype=np.float64)
    top_diff = np.empty(8, dtype=np.float64)
    bot_diff = np.empty(8, dtype=np.float64)

    if detail_motion:
        if has_left:
            nb = c - 1
            for k in range(8):
                left_diff[k] = md_lum[cur_colors[nb, k, 7]] - md_lum[plain_colors[nb, k * 8 + 7]]
        if has_right:
            nb = c + 1
            for k in range(8):
                right_diff[k] = md_lum[cur_colors[nb, k, 0]] - md_lum[plain_colors[nb, k * 8]]
        if has_top:
            nb = c - tcols
            for k in range(8):
                top_diff[k] = md_lum[cur_colors[nb, 7, k]] - md_lum[plain_colors[nb, 56 + k]]
        if has_bot:
            nb = c + tcols
            for k in range(8):
                bot_diff[k] = md_lum[cur_colors[nb, 0, k]] - md_lum[plain_colors[nb, k]]

    for p in range(n_cand):
        pat = colors[p]
        ym, yp = _pairwise_tile_metrics(f3_dy, pat, target, lanes, True)
        cm, _ = _pairwise_tile_metrics(f3_dc, pat, target, lanes, False)
        bias_sum = 0.0
        for idx in range(64):
            bias_sum += md_lum[pat[idx]] - target_lum[idx]
        score = ym + 0.3 * yp + 0.5 * cm + 0.8 * abs(bias_sum / 64.0)

        if detail_motion:
            seam_err = 0.0
            if has_left:
                s = 0.0
                for k in range(8):
                    idx = k * 8
                    res = md_lum[pat[idx]] - target_lum[idx]
                    s += abs(res - left_diff[k])
                seam_err += s * 0.125
            if has_right:
                s = 0.0
                for k in range(8):
                    idx = k * 8 + 7
                    res = md_lum[pat[idx]] - target_lum[idx]
                    s += abs(res - right_diff[k])
                seam_err += s * 0.125
            if has_top:
                s = 0.0
                for k in range(8):
                    idx = k
                    res = md_lum[pat[idx]] - target_lum[idx]
                    s += abs(res - top_diff[k])
                seam_err += s * 0.125
            if has_bot:
                s = 0.0
                for k in range(8):
                    idx = 56 + k
                    res = md_lum[pat[idx]] - target_lum[idx]
                    s += abs(res - bot_diff[k])
                seam_err += s * 0.125
            score += 0.6 * seam_err / edge_div

        scores[p] = score
    return scores


@njit(nogil=True, cache=True)
def local_luma_range_numba(rgb):
    """Compute clipped 3x3 integer luma range without padded neighbour views."""
    h, w, _ = rgb.shape
    luma = np.empty((h, w), dtype=np.int16)
    for y in range(h):
        for x in range(w):
            r = np.int32(rgb[y, x, 0])
            g = np.int32(rgb[y, x, 1])
            b = np.int32(rgb[y, x, 2])
            luma[y, x] = np.int16((77 * r + 150 * g + 29 * b + 128) >> 8)
    out = np.empty((h, w), dtype=np.int16)
    for y in range(h):
        y0 = y - 1 if y > 0 else 0
        y1 = y + 1 if y + 1 < h else h - 1
        for x in range(w):
            x0 = x - 1 if x > 0 else 0
            x1 = x + 1 if x + 1 < w else w - 1
            mn = luma[y, x]
            mx = mn
            for ny in range(y0, y1 + 1):
                for nx in range(x0, x1 + 1):
                    v = luma[ny, nx]
                    if v < mn:
                        mn = v
                    if v > mx:
                        mx = v
            out[y, x] = mx - mn
    return out


@njit(nogil=True, cache=True)
def expand_edge_range_numba(values, radius):
    """Spread strongest local edge range outward by radius pixels."""
    h, w = values.shape
    out = np.empty((h, w), dtype=values.dtype)
    for y in range(h):
        y0 = y - radius if y >= radius else 0
        y1 = y + radius if y + radius < h else h - 1
        for x in range(w):
            x0 = x - radius if x >= radius else 0
            x1 = x + radius if x + radius < w else w - 1
            mx = values[y, x]
            for ny in range(y0, y1 + 1):
                for nx in range(x0, x1 + 1):
                    v = values[ny, nx]
                    if v > mx:
                        mx = v
            out[y, x] = mx
    return out


@njit(nogil=True, cache=True)
def edge_strengths_numba(tiles):
    """Compute integer local-gradient numerator for (T, 64, 3) RGB333 tiles."""
    n_tiles = tiles.shape[0]
    out = np.empty((n_tiles, 64), dtype=np.uint8)
    for t in range(n_tiles):
        for y in range(8):
            row_off = y * 8
            for x in range(8):
                idx = row_off + x
                r = np.int32(tiles[t, idx, 0])
                g = np.int32(tiles[t, idx, 1])
                b = np.int32(tiles[t, idx, 2])
                acc = np.int32(0)
                if x < 7:
                    acc += abs(r - np.int32(tiles[t, idx + 1, 0])) + abs(g - np.int32(tiles[t, idx + 1, 1])) + abs(b - np.int32(tiles[t, idx + 1, 2]))
                if x > 0:
                    acc += abs(r - np.int32(tiles[t, idx - 1, 0])) + abs(g - np.int32(tiles[t, idx - 1, 1])) + abs(b - np.int32(tiles[t, idx - 1, 2]))
                if y < 7:
                    acc += abs(r - np.int32(tiles[t, idx + 8, 0])) + abs(g - np.int32(tiles[t, idx + 8, 1])) + abs(b - np.int32(tiles[t, idx + 8, 2]))
                if y > 0:
                    acc += abs(r - np.int32(tiles[t, idx - 8, 0])) + abs(g - np.int32(tiles[t, idx - 8, 1])) + abs(b - np.int32(tiles[t, idx - 8, 2]))
                out[t, idx] = np.uint8(acc)
    return out


@njit(nogil=True, cache=True)
def flatten_low_detail_numba(tiles, flatten_std):
    """Flatten low-detail tiles and compute per-tile detail in one pass."""
    n_tiles = tiles.shape[0]
    out = tiles.copy()
    detail = np.empty(n_tiles, dtype=np.float64)
    for t in range(n_tiles):
        s0 = 0.0
        s1 = 0.0
        s2 = 0.0
        for p in range(64):
            s0 += float(tiles[t, p, 0])
            s1 += float(tiles[t, p, 1])
            s2 += float(tiles[t, p, 2])
        m0 = s0 / 64.0
        m1 = s1 / 64.0
        m2 = s2 / 64.0
        v0 = 0.0
        v1 = 0.0
        v2 = 0.0
        for p in range(64):
            d0 = float(tiles[t, p, 0]) - m0
            d1 = float(tiles[t, p, 1]) - m1
            d2 = float(tiles[t, p, 2]) - m2
            v0 += d0 * d0
            v1 += d1 * d1
            v2 += d2 * d2
        det = (np.sqrt(v0 / 64.0) + np.sqrt(v1 / 64.0) + np.sqrt(v2 / 64.0)) / 3.0
        detail[t] = det
        if det < flatten_std:
            c0 = np.uint8(np.round(m0))
            c1 = np.uint8(np.round(m1))
            c2 = np.uint8(np.round(m2))
            for p in range(64):
                out[t, p, 0] = c0
                out[t, p, 1] = c1
                out[t, p, 2] = c2
    return out, detail


@njit(nogil=True, cache=True)
def frame_metrics_numba(image_u8, uni_tol, trows, tcols):
    """Compute (dark, uniform, fade_probe) from RGB888 uint8 without float64 frame arrays."""
    h, w, _ = image_u8.shape
    n_pixels = h * w
    dark_count = 0
    sum_r = 0.0
    sum_g = 0.0
    sum_b = 0.0
    fade_probe = np.empty((trows * tcols, 3), dtype=np.float32)

    for tr in range(trows):
        y_base = tr * 8
        row_cell_base = tr * tcols
        for tc in range(tcols):
            x_base = tc * 8
            cell_r = 0
            cell_g = 0
            cell_b = 0
            for dy in range(8):
                y = y_base + dy
                for dx in range(8):
                    x = x_base + dx
                    r = np.int32(image_u8[y, x, 0])
                    g = np.int32(image_u8[y, x, 1])
                    b = np.int32(image_u8[y, x, 2])
                    cell_r += r
                    cell_g += g
                    cell_b += b
                    if (0.299 * r + 0.587 * g + 0.114 * b) < 32.0:
                        dark_count += 1
            cell_idx = row_cell_base + tc
            fade_probe[cell_idx, 0] = np.float32(cell_r / 64.0)
            fade_probe[cell_idx, 1] = np.float32(cell_g / 64.0)
            fade_probe[cell_idx, 2] = np.float32(cell_b / 64.0)
            sum_r += float(cell_r)
            sum_g += float(cell_g)
            sum_b += float(cell_b)

    mean_r = sum_r / float(n_pixels)
    mean_g = sum_g / float(n_pixels)
    mean_b = sum_b / float(n_pixels)
    tol_sq = uni_tol * uni_tol
    uni_count = 0
    for y in range(h):
        for x in range(w):
            dr = float(image_u8[y, x, 0]) - mean_r
            dg = float(image_u8[y, x, 1]) - mean_g
            db = float(image_u8[y, x, 2]) - mean_b
            if np.sqrt(dr * dr + dg * dg + db * db) < uni_tol:
                uni_count += 1

    dark = float(dark_count) / float(n_pixels)
    uniform = float(uni_count) / float(n_pixels)
    return dark, uniform, fade_probe


_BAYER_SCALE_LUT = (np.arange(256, dtype=np.float32) * np.float32(7.0 / 255.0))
_BAYER_BASE_LUT = np.floor(_BAYER_SCALE_LUT).astype(np.uint8)
_BAYER_FRAC_LUT = (_BAYER_SCALE_LUT - np.floor(_BAYER_SCALE_LUT)).astype(np.float32)


@njit(nogil=True, cache=True)
def quantize_bayer_threshold_numba(rgb, threshold, base_lut, frac_lut):
    """Apply precomputed 2D threshold table to RGB888 using 256-entry float32 LUTs."""
    h, w, _ = rgb.shape
    out = np.empty((h, w, 3), dtype=np.uint8)
    for y in range(h):
        for x in range(w):
            thr = threshold[y, x]
            for c in range(3):
                v = rgb[y, x, c]
                b = base_lut[v]
                if frac_lut[v] > thr and b < 7:
                    out[y, x, c] = b + 1
                else:
                    out[y, x, c] = b
    return out


@njit(nogil=True, cache=True)
def quantize_attenuated_bayer_numba(
    rgb, bayer, edge_range, start, full, base_offset, scale_factor, base_lut, frac_lut
):
    """Fused edge-attenuated / soft-bayer RGB333 quantization without 3D float32 temporaries."""
    h, w, _ = rgb.shape
    out = np.empty((h, w, 3), dtype=np.uint8)
    inv_span = np.float32(1.0 / (full - start))
    full_f = np.float32(full)
    half = np.float32(0.5)
    base_off = np.float32(base_offset)
    scale_f = np.float32(scale_factor)
    for y in range(h):
        for x in range(w):
            amt = (full_f - np.float32(edge_range[y, x])) * inv_span
            if amt < np.float32(0.0):
                amt = np.float32(0.0)
            elif amt > np.float32(1.0):
                amt = np.float32(1.0)
            eff_amt = base_off + scale_f * amt
            thr = half + (bayer[y, x] - half) * eff_amt
            for c in range(3):
                v = rgb[y, x, c]
                b = base_lut[v]
                if frac_lut[v] > thr and b < 7:
                    out[y, x, c] = b + 1
                else:
                    out[y, x, c] = b
    return out
