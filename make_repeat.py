"""Fixture FILM largo: repetición explícita de fotogramas codificados reales."""
from pathlib import Path
import struct
import sys
import math

def repeat_cin(source, target, duration, fps):
    data = Path(source).read_bytes()
    hs = struct.unpack_from('>I', data, 4)[0]
    n = struct.unpack_from('>I', data, 48)[0]
    entries = [struct.unpack_from('>IIII', data, 52+16*i) for i in range(n)]
    frames = [data[hs+o:hs+o+l] for o,l,t,d in entries if t != 0xFFFFFFFF]
    # Audio de fixture: tono original, sin la cola de silencio del segmento.
    audio = b''.join(data[hs+o:hs+o+l] for o,l,t,d in entries if t == 0xFFFFFFFF)
    audio = audio[:round(len(frames)/fps*16276)]
    length = math.ceil(duration*16276/16384)*16384
    audio = (audio * math.ceil(length / len(audio)))[:length]
    chunks = [audio[i:i+16384] for i in range(0, len(audio), 16384)]
    desc = []
    payloads = []
    offset = ai = 0
    audio_time = -0.4*600*16384/16000
    step = 600/fps*16276/16000
    for i in range(round(duration*fps)):
        time = i*step
        while ai < len(chunks) and time > audio_time:
            chunk = chunks[ai]
            desc.append((offset,len(chunk),0xFFFFFFFF,1))
            payloads.append(chunk)
            offset += len(chunk)
            ai += 1
            audio_time += 600*16384/16000
        frame = frames[i % len(frames)]
        desc.append((offset,len(frame),int(time),int((i+1)*step)-int(time)))
        payloads.append(frame)
        offset += len(frame)
    header = bytearray(data[:36])
    struct.pack_into('>I',header,4,68+16*len(desc))
    header.extend(struct.pack('>4sIII',b'STAB',32+16*len(desc),600,len(desc)))
    for entry in desc:
        header.extend(struct.pack('>IIII',*entry))
    header.extend(bytes(16))
    with Path(target).open('wb') as f:
        f.write(header)
        for payload in payloads:
            f.write(payload)

if __name__ == '__main__':
    repeat_cin(sys.argv[1],sys.argv[2],float(sys.argv[3]),int(sys.argv[4]))
