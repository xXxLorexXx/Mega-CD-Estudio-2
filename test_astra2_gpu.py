from pathlib import Path
import sys,time,json
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
import megacd_gpu as g
rng=np.random.default_rng(229);start=time.perf_counter()
for dim in (12,24):
 for codes in (1,16,256):
  queries=rng.integers(0,8,(521,dim)).astype(np.float32)
  book=rng.integers(0,8,(codes,dim)).astype(np.float32)
  # Repetir un vector para comprobar también los empates.
  if codes>1:book[-1]=book[0]
  expected=np.argmin(((queries[:,None,:]-book[None,:,:])**2).sum(axis=2),axis=1).astype(np.uint8)
  actual=g.nearest(queries,book)
  assert actual is not None,g.stats()
  assert np.array_equal(expected,actual)
for palettes in (1,2,4):
 target=rng.integers(0,8,(768,64,3)).astype(np.float32)
 prior=rng.integers(0,16,(768,64),dtype=np.uint8)
 pal=rng.integers(0,8,(palettes,16,3)).astype(np.float32)
 diff=target[None,:,:,:]-pal[:,prior]
 sq=(diff*diff).sum(axis=3);mean=sq.mean(axis=2);maximum=sq.max(axis=2)
 score=mean+.15*maximum
 actual=g.previous(target,prior,pal)
 assert actual is not None,g.stats()
 for a,b in zip((mean,maximum,score),actual):assert np.array_equal(a,b),np.max(abs(a-b))
print('CUDA correcto:',g.stats(),'s',time.perf_counter()-start,flush=True)
