"""Assemble specialized part players and the untimed multipart dispatcher."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import analysis_logs
import ima_adpcm
import player_constants
import sp_extension
from multipart import ROOT, digest, run_logged, stem_lock, write_json

IP_BYTES = 0x5000
WORD_BYTES = 0x2000
STUB = 0x7F50
STATE = 0xFF9900
SECTOR = 2048
SP_MODULE_LIMIT = 0x1400
SP_MEDIA_OFFSET = 0x1800
SP_PACKAGE_BASE = 0x67000
SP_PACKAGE_LIMIT = 0x10000


def tool(name):
    prefix = os.environ.get("M68K_PREFIX", str(Path.home()/"toolchains/mars/m68k-elf/bin/m68k-elf-"))
    candidate = prefix + name
    if not shutil.which(candidate):
        raise FileNotFoundError(f"missing tool: {candidate}")
    return candidate


def symbol(obj, name, base=0):
    output = subprocess.check_output([tool("nm"), "--defined-only", str(obj)], text=True)
    matches = [int(line.split()[0], 16) for line in output.splitlines()
               if len(line.split()) == 3 and line.split()[2] == name]
    if len(matches) != 1:
        raise ValueError(f"missing or ambiguous symbol {name} in {obj}")
    return base + matches[0]


def names(number):
    return tuple(f"P{number:04}{suffix}" for suffix in ("IP.BIN", "SP.BIN", "HDR.DAT", "BOD.DAT"))


def main_include(sizes, entries):
    return (f".equ LOADER_COUNT, {len(sizes)}\n.data\n.align 2\nloader_ip_sizes:\n.word "
            + ",".join(map(str, sizes)) + "\n.align 2\nmultipart_player_entries:\n.long "
            + ",".join(map(str, entries)) + "\n")


def sub_include(count, entries):
    constants = {
        "LOADER_COUNT": count, "LOADER_IP_IMAGE_BYTES": IP_BYTES,
        "LOADER_IP_IMAGE_SECTORS": IP_BYTES//2048, "LOADER_SP_WORD_SECTORS": WORD_BYTES//2048,
        "LOADER_IMAGE_OFF": 0, "PLAYER_IP_STAGE_OFF": IP_BYTES,
        "LOADER_SP_WORD_OFF": 0x1E000, "LOADER_SP_WORD_ENTRY": 0xDE000,
        "MULTIPART_LOADER_INFO_ADDR": 0x7F20, "MULTIPART_SELECTED_SP_INFO_ADDR": 0x7F42,
        "MULTIPART_WORD_SWAP_STUB": STUB, "MULTIPART_LOADER_RUNTIME_ADDR": 0x7F70,
        "PLAYER_SP_BASE": 0x6000, "CMD_LOADER_LOAD": 0x52,
        "PLAYER_SP_MODULE_LIMIT": SP_MODULE_LIMIT,
        "PLAYER_SP_MEDIA_OFFSET": SP_MEDIA_OFFSET,
        "PLAYER_SP_PACKAGE_LIMIT": SP_PACKAGE_LIMIT,
        "STAT_LOADER_READY": 0x8005, "STAT_LOADER_IP_READY": 0x8006,
        "STAT_PLAYER_READY": 0x8007,
    }
    lines = [f".equ {key}, {value}" for key, value in constants.items()]
    lines += ['.data', '.align 2', 'loader_ip_file: .asciz "PARTIP.BIN"',
              'loader_sp_file: .asciz "PARTSP.BIN"', '.align 2']
    for column, kind in enumerate(("ip", "sp", "header", "body")):
        lines += [f"loader_player_{kind}_names:", ".long " + ",".join(
            f"part_{number}_{kind}" for number in range(1, count+1))]
        for number in range(1, count+1):
            lines.append(f'part_{number}_{kind}: .asciz "{names(number)[column]}"')
        lines += ['.align 2']
    lines += ['multipart_sp_entries:', '.long ' + ','.join(map(str, entries)), '']
    return '\n'.join(lines)


def assemble(work, region, *, bootstrap=True):
    directory = work / "build"
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT/f"boot/sec_{region}.bin", directory/"security.bin")
    shutil.copyfile(ROOT/f"boot/region_{region}.inc", directory/"disc_region.inc")
    commands = []
    for source, linker, target, maximum in (
        ("multipart_ip", "multipart_ip", "multipart_ip", IP_BYTES),
        ("multipart_sp", "multipart_sp_word", "loader_word", WORD_BYTES),
        *(([("multipart_boot_sp", "sp", "multipart_boot_sp", 5120)]) if bootstrap else []),
    ):
        obj = directory/f"{source}.o"
        binary = directory/f"{target}.bin"
        commands.extend([
            [tool("as"), "-m68000", "--register-prefix-optional", "--bitwise-or",
             f"-I{directory}", "-Iboot", f"boot/{source}.s", "-o", str(obj)],
            [tool("ld"), "-nostdlib", "--oformat", "binary", "-T", f"cfg/{linker}.ld",
             "-o", str(binary), str(obj)],
        ])
        for command in commands[-2:]:
            run_logged(command, work.name + "-assemble")
        if not 0 < binary.stat().st_size <= maximum:
            raise ValueError(f"{binary} exceeds its {maximum}-byte slot")
        if source != "multipart_boot_sp":
            binary.write_bytes(binary.read_bytes().ljust(maximum, b"\0"))
    if bootstrap:
        obj = directory/"multipart_boot.o"
        run_logged([tool("as"), "-m68000", "--register-prefix-optional", "--bitwise-or",
                    f"-I{directory}", "-Iboot", "boot/multipart_boot.s", "-o", str(obj)],
                   work.name + "-boot")
        run_logged([tool("objcopy"), "-O", "binary", str(obj), str(directory/"multipart_boot.bin")],
                   work.name + "-boot")
        if (directory/"multipart_boot.bin").stat().st_size != 32768:
            raise ValueError("multipart boot system area must be 32768 bytes")
    return directory


def verify_iso(iso, staged):
    """Independently read ISO records and authenticate every staged byte."""
    def paired(record, offset):
        little = int.from_bytes(record[offset:offset+4], "little")
        big = int.from_bytes(record[offset+4:offset+8], "big")
        if little != big:
            raise ValueError("ISO directory endian fields disagree")
        return little
    size = iso.stat().st_size
    if size > 360000*2048 or size % 2048:
        raise ValueError("ISO exceeds disc capacity or has an incomplete sector")
    with iso.open("rb") as stream:
        stream.seek(16*2048)
        pvd = stream.read(2048)
        if pvd[:7] != b"\x01CD001\x01":
            raise ValueError("missing ISO primary descriptor")
        root = pvd[156:190]
        lba, count = paired(root, 2), paired(root, 10)
        if not 0 < count <= 65536 or lba*2048+count > size:
            raise ValueError("ISO root directory is outside the loader's bounds")
        stream.seek(lba*2048)
        directory = stream.read(count)
        files = {}
        offset = 0
        while offset < count:
            length = directory[offset]
            if not length:
                offset = (offset//2048+1)*2048
                continue
            record = directory[offset:offset+length]
            if (length < 34 or length % 2 or len(record) != length
                    or offset % 2048+length > 2048 or 33+record[32] > length):
                raise ValueError("invalid ISO directory record")
            if not record[25] & 2:
                name = record[33:33+record[32]].decode("ascii").removesuffix(";1")
                if name in files:
                    raise ValueError("duplicate ISO filename")
                start, length_bytes = paired(record, 2), paired(record, 10)
                if not length_bytes or start*2048+length_bytes > size:
                    raise ValueError("ISO file exceeds disc bounds")
                files[name] = start, length_bytes
            offset += length
        if set(files) != {path.name for path in staged.iterdir()}:
            raise ValueError("ISO file list differs from completed multipart staging")
        for path in staged.iterdir():
            start, count = files[path.name]
            if count != path.stat().st_size:
                raise ValueError(f"ISO file size differs: {path.name}")
            stream.seek(start*2048)
            import hashlib
            actual = hashlib.sha256()
            remaining = count
            while remaining:
                block = stream.read(min(1024*1024, remaining))
                if not block:
                    raise ValueError("truncated ISO payload")
                actual.update(block)
                remaining -= len(block)
            if actual.hexdigest() != digest(path):
                raise ValueError(f"ISO payload differs: {path.name}")


def package_sub_player(module, header, output):
    """Read the player and its initial metadata in one loader operation."""
    code = module.read_bytes()
    if not 32 <= len(code) <= SP_MODULE_LIMIT or len(code) % 2:
        raise ValueError(f"Sub player exceeds its fixed resident slot: {module}")
    with header.open('rb') as source:
        packet = source.read(SECTOR)
        fields = player_constants.parse_header_sector(packet)
        media_bytes = (fields.paltab_sec + fields.dic_sectors) * SECTOR
        media = source.read(media_bytes)
    if len(media) != media_bytes:
        raise ValueError(f"Truncated startup metadata: {header}")
    prefix = code.ljust(SP_MODULE_LIMIT, b'\0') + len(code).to_bytes(2, 'big')
    size = SP_MEDIA_OFFSET + len(packet) + len(media)
    if size > SP_PACKAGE_LIMIT:
        raise ValueError(f"Startup data exceeds the loader buffer: {header}")
    output.write_bytes(prefix.ljust(SP_MEDIA_OFFSET, b'\0') + packet + media)


def build(plan, profiles, records, *, debug, region, disc=None, numbers=None):
    """Build one physical disc. `disc` = (index, count) when the film spans
    several CDs; `numbers` are then the film-wide part numbers on this disc."""
    numbers = list(numbers) if numbers is not None else list(range(1, len(profiles) + 1))
    if len(numbers) != len(profiles):
        raise ValueError("part numbers do not match the disc's parts")
    work = ROOT / "tmp" / plan["stem"]
    if disc is not None:
        work = work / f"disc{disc[0]}"
    directory = work/"build"
    directory.mkdir(parents=True, exist_ok=True)
    count = len(profiles)
    (directory/"multipart_ip.inc").write_text(main_include([2]*count, [0]*count))
    (directory/"multipart_sp.inc").write_text(sub_include(count, [0]*count))
    assemble(work, region, bootstrap=False)
    reentry = symbol(directory/"multipart_ip.o", "multipart_resume", 0xFF0000)
    staged = work/"disc"
    staged.mkdir(exist_ok=True)
    for path in staged.iterdir():
        if not path.is_file():
            raise ValueError(f"unexpected multipart staging directory: {path}")
        path.unlink()
    sizes, entries, sub_entries, items = [], [], [], []
    for number, (profile, record) in enumerate(zip(profiles, records), 1):
        module = ROOT/profile.build_dir
        module.mkdir(parents=True, exist_ok=True)
        (module/"multipart_player.inc").write_text(
            f".equ MULTIPART_RETURN_ENTRY, {reentry}\n"
            f".equ MULTIPART_MEDIA_PREFIX, {SP_PACKAGE_BASE + SP_MEDIA_OFFSET}\n")
        with stem_lock(profile.sim_stem):
            run_logged(["make", "movieplay-module", f"CONFIG={profile.path.relative_to(ROOT)}",
                        f"DEBUG={int(debug)}", "MULTIPART=1", f"SECURITY_REGION={region}",
                        "--assume-old=moviepack"], f"{plan['stem']}-part{numbers[number-1]:03}-player")
        folder = ROOT/profile.artifact_dir
        expected = sp_extension.adpcm_preload_image(
            ima_adpcm.full_tables(), (module/"movieplay_sp_ext.bin").read_bytes())
        if (folder/"HEADER.DAT").read_bytes().count(expected) != 1:
            raise ValueError(f"part {number}: packed boot extension differs; repacking is required")
        ip, sp = module/"movieplay_ip.bin", module/"movieplay_sp.bin"
        restore_bytes = (symbol(module/"movieplay_ip.o", "multipart_restore_end")
                         - symbol(module/"movieplay_ip.o", "multipart_restore"))
        if not 0 < restore_bytes <= STATE-0xFF8880 or restore_bytes % 2:
            raise ValueError(f"part {number}: Main restore overlaps persistent state")
        ip_entry = symbol(module/"movieplay_ip.o", "ip_entry", 0xFF0000)
        sp_entry = symbol(module/"movieplay_sp.o", "multipart_player_entry", 0x6000)
        if not 0xFF0000 <= ip_entry < 0xFF0000+ip.stat().st_size:
            raise ValueError("Main entry is outside the part module")
        if not 0x6000 <= sp_entry < 0x6000+sp.stat().st_size:
            raise ValueError("Sub entry is outside the part module")
        sizes.append(ip.stat().st_size)
        entries.append(ip_entry)
        sub_entries.append(sp_entry)
        files = {}
        header = folder/"HEADER.DAT"
        for path, name in zip((ip, sp, header, folder/"BODY.DAT"), names(number)):
            output = staged/name
            if path == sp:
                package_sub_player(sp, header, output)
            else:
                shutil.copyfile(path, output)
            files[name] = {"bytes": output.stat().st_size, "sha256": digest(output)}
        items.append({"part": numbers[number-1], "disc_part": number,
                      "profile": str(profile.path), "frames": record["frames"],
                      "profile_sha256": profile.sha256,
                      "range": plan["ranges"][numbers[number-1]-1], "files": files,
                      "ip_entry": ip_entry, "sp_entry": sp_entry})
    (directory/"multipart_ip.inc").write_text(main_include(sizes, entries))
    (directory/"multipart_sp.inc").write_text(sub_include(count, sub_entries))
    assemble(work, region)
    if symbol(directory/"multipart_ip.o", "multipart_resume", 0xFF0000) != reentry:
        raise ValueError("multipart reentry moved after filling dispatch tables")
    for obj, first, last, capacity in (
        (directory/"multipart_ip.o", "multipart_copy", "multipart_copy_end", STATE-0xFF8880),
        (directory/"multipart_boot_sp.o", "multipart_word_swap_stub_image", "multipart_word_swap_stub_image_end", 20),
        (directory/"multipart_boot_sp.o", "multipart_return_image", "multipart_return_image_end", 0xD800-0xD640),
    ):
        length = symbol(obj, last)-symbol(obj, first)
        if not 0 < length <= capacity or length % 2:
            raise ValueError(f"resident stub {first} exceeds its checked memory: {length}/{capacity}")
    shutil.copyfile(directory/"multipart_ip.bin", staged/"PARTIP.BIN")
    shutil.copyfile(directory/"loader_word.bin", staged/"PARTSP.BIN")
    iso_tool = shutil.which("mkisofs") or shutil.which("genisoimage")
    if not iso_tool:
        raise FileNotFoundError("mkisofs/genisoimage is required")
    suffix = ("" if region == "jp" else f"_{region}") + ("" if debug else "_release")
    label = "" if disc is None else f"-disc{disc[0]}of{disc[1]}"
    iso = ROOT/"out"/f"{plan['stem']}{label}{suffix}.iso"
    pending = work/"multipart.pending.iso"
    volume = "CAVC_MULTIPART" if disc is None else f"CAVC_DISC{disc[0]}OF{disc[1]}"
    run_logged([iso_tool, "-iso-level", "1", "-G", directory/"multipart_boot.bin",
                "-pad", "-V", volume, "-o", pending, staged], plan["stem"]+"-iso")
    if pending.stat().st_size > 360_000 * 2048:
        pending.unlink()
        raise ValueError(f"disc image exceeds an 80-minute CD: {iso.name}")
    verify_iso(pending, staged)
    os.replace(pending, iso)
    cue = iso.with_suffix(".cue")
    cue.write_text(f'FILE "{iso.name}" BINARY\n  TRACK 01 MODE1/2048\n    INDEX 01 00:00:00\n')
    manifest = {"schema": 1, "identity": plan["identity"], "versions": analysis_logs.av_versions(),
                "profile": plan["profile"], "parts": count, "stem": plan["stem"],
                "debug": debug, "region": region, "iso": str(iso), "cue": str(cue),
                "disc": list(disc) if disc is not None else None,
                "iso_sha256": digest(iso), "items": items,
                "trace_pc_start": symbol(directory/"multipart_ip.o", "multipart_trace_begin", 0xFF0000),
                "trace_pc_end": symbol(directory/"multipart_ip.o", "multipart_trace_end", 0xFF0000),
                "state_address": STATE}
    receipt = ROOT/"out"/plan["stem"]/f"multipart{label}{suffix}.json"
    write_json(receipt, manifest)
    print(f"multipart: verified ISO {iso}\nCUE {cue}\nManifest {receipt}", flush=True)
    return manifest
