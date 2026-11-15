# -*- coding: utf-8 -*-
"""Aceleración CPU opcional; conserva el formato y dispone de ruta NumPy."""
import os
import numpy as np

AVAILABLE = False
try:
    if os.environ.get('MEGACD_DISABLE_JIT') != '1':
        from numba import njit
        AVAILABLE = True
except Exception:
    pass
if not AVAILABLE:
    def njit(*args, **kwargs):
        return lambda fn: fn


FAILURE = ''

def initialize():
    """Comprueba LLVM y permite continuar sin JIT si el equipo no lo admite."""
    global AVAILABLE, FAILURE
    if not AVAILABLE:
        return False
    try:
        neutral_pixels(np.zeros((1,1,3),np.uint8),np.ones(3,np.float32))
        palette_counts(np.zeros(512,np.float64))
    except Exception as error:
        FAILURE=str(error)
        AVAILABLE=False
        for name,value in list(globals().items()):
            if hasattr(value,'py_func'):
                globals()[name]=value.py_func
    return AVAILABLE


@njit(cache=False, nogil=True)
def neutral_pixels(rgb, gains):
    out = np.empty_like(rgb)
    for y in range(rgb.shape[0]):
        for x in range(rgb.shape[1]):
            r, g, b = np.float32(rgb[y,x,0]), np.float32(rgb[y,x,1]), np.float32(rgb[y,x,2])
            a, c, d = r/gains[0], g/gains[1], b/gains[2]
            lo, hi = min(a,c,d), max(a,c,d)
            gray = (a+c+d)-lo-hi
            # Ordenación de tres elementos, sin pérdida por sumas canceladas.
            gray = max(min(a,c), min(max(a,c),d))
            er = min(np.float32(255),max(np.float32(0),gray*gains[0]))
            eg = min(np.float32(255),max(np.float32(0),gray*gains[1]))
            eb = min(np.float32(255),max(np.float32(0),gray*gains[2]))
            if max(abs(r-er),abs(g-eg),abs(b-eb)) <= np.float32(1.5):
                rr=gg=bb=gray
            else:
                w = min(np.float32(1),max(np.float32(0),(np.float32(30)-(hi-lo))/np.float32(22)))
                lum = r*np.float32(.299)+g*np.float32(.587)+b*np.float32(.114)
                rr=r+(lum-r)*w; gg=g+(lum-g)*w; bb=b+(lum-b)*w
            out[y,x,0]=np.uint8(min(255,max(0,np.rint(rr))))
            out[y,x,1]=np.uint8(min(255,max(0,np.rint(gg))))
            out[y,x,2]=np.uint8(min(255,max(0,np.rint(bb))))
    return out


@njit(cache=False, nogil=True)
def _pair_sum(a, n):
    if n < 8:
        value = -0.0
        for i in range(n): value += a[i]
        return value
    if n <= 128:
        v=a[:8].copy()
        stop=n-n%8
        for i in range(8,stop,8):
            for k in range(8): v[k] += a[i+k]
        value=((v[0]+v[1])+(v[2]+v[3]))+((v[4]+v[5])+(v[6]+v[7]))
        for i in range(stop,n): value += a[i]
        return value
    mid=(n//2)//8*8
    return _pair_sum(a[:mid],mid)+_pair_sum(a[mid:n],n-mid)


@njit(cache=False, nogil=True)
def palette_counts(hist):
    ids=np.empty(512,np.int32); weights=np.empty(512,np.float64)
    colors=np.empty((512,3),np.float64); n=0
    for i in range(1,512):
        if hist[i] > 0:
            ids[n]=i;weights[n]=hist[i]
            colors[n,0]=(i>>6)&7;colors[n,1]=(i>>3)&7;colors[n,2]=i&7
            n+=1
    pal=np.zeros((16,3),np.int32)
    if n==0:return pal
    if n<=15:
        order=np.arange(n)
        for i in range(1,n):
            j=i
            while j>0:
                a=order[j-1];b=order[j]
                la=colors[a,0]*299+colors[a,1]*587+colors[a,2]*114
                lb=colors[b,0]*299+colors[b,1]*587+colors[b,2]*114
                if la<=lb:break
                order[j-1],order[j]=order[j],order[j-1];j-=1
        for i in range(15):
            k=order[min(i,n-1)]
            for c in range(3):pal[i+1,c]=int(colors[k,c])
        return pal
    ctr=np.zeros((16,3),np.float64)
    first=0
    for i in range(1,n):
        if weights[i]>weights[first]: first=i
    ctr[1]=colors[first]
    mins=np.empty(n,np.float64)
    for i in range(n):
        d0=0.;d1=0.
        for c in range(3):
            d0+=colors[i,c]*colors[i,c]
            d1+=(colors[i,c]-ctr[1,c])**2
        mins[i]=min(d0,d1)
    for k in range(2,16):
        best=0;score=-1.
        for i in range(n):
            sc=mins[i]*np.sqrt(weights[i])
            if sc>score:score=sc;best=i
        ctr[k]=colors[best]
        for i in range(n):
            d=0.
            for c in range(3):d+=(colors[i,c]-ctr[k,c])**2
            mins[i]=min(mins[i],d)
    labels=np.empty(n,np.int32); wk=np.empty(n,np.float64)
    for iteration in range(6):
        for i in range(n):
            best=0;score=1.e30
            for k in range(16):
                d=0.
                for c in range(3):d+=(colors[i,c]-ctr[k,c])**2
                if d<score:score=d;best=k
            labels[i]=best
        for k in range(1,16):
            nr=ng=nb=0.;count=0
            for i in range(n):
                if labels[i]==k:
                    w=weights[i];wk[count]=w;count+=1
                    nr+=colors[i,0]*w;ng+=colors[i,1]*w;nb+=colors[i,2]*w
            if count:
                den=_pair_sum(wk,count)
                ctr[k,0]=np.rint(nr/den);ctr[k,1]=np.rint(ng/den);ctr[k,2]=np.rint(nb/den)
    for i in range(1,16):
        for c in range(3):pal[i,c]=int(min(7,max(0,np.rint(ctr[i,c]))))
    for i in range(2,16):
        j=i
        while j>1:
            la=pal[j-1,0]*299+pal[j-1,1]*587+pal[j-1,2]*114
            lb=pal[j,0]*299+pal[j,1]*587+pal[j,2]*114
            if la<=lb:break
            temp=pal[j-1].copy();pal[j-1]=pal[j];pal[j]=temp;j-=1
    return pal


def fit_methods(methods, m2, m3, cb32, cb16, tpals, best_m0, score_m0,
                err2, err3, border_mask, previous, refresh, palette_count, cap, allow_lossy_reuse=True):
    """Límite real en bytes; compacta diccionarios sin cambiar sus colores."""
    refs2=np.bincount(m2[methods==2].ravel(),minlength=len(cb32)).astype(np.int32)
    refs3=np.bincount(m3[methods==3].ravel(),minlength=len(cb16)).astype(np.int32)
    size=12+palette_count*32+192+8+192+int(np.count_nonzero(refs2))*4+int(np.count_nonzero(refs3))*2
    lengths=np.array([0,32,8,16],dtype=np.int32)
    size+=int(lengths[methods].sum())
    def change(t,new):
        nonlocal size
        old=int(methods[t]);size+=int(lengths[new]-lengths[old])
        if old==2:
            ids,counts=np.unique(m2[t],return_counts=True)
            for idx,count in zip(ids,counts):
                refs2[idx]-=count
                if refs2[idx]==0:size-=4
        if old==3:
            ids,counts=np.unique(m3[t],return_counts=True)
            for idx,count in zip(ids,counts):
                refs3[idx]-=count
                if refs3[idx]==0:size-=2
        if new==2:
            ids,counts=np.unique(m2[t],return_counts=True)
            for idx,count in zip(ids,counts):
                if refs2[idx]==0:size+=4
                refs2[idx]+=count
        methods[t]=new
        if new==0:tpals[t]=best_m0[t]
    if cap is not None and size>cap:
        candidates=np.where((methods==1)|(methods==3))[0]
        current=np.where(methods[candidates]==1,0,err3[candidates])
        costs=(err2[candidates]-current)/np.maximum(1,lengths[methods[candidates]]-8)
        for t in candidates[np.argsort(costs,kind='stable')]:
            if size<=cap:break
            change(t,2)
        if size>cap and previous is not None and allow_lossy_reuse:
            eligible=(methods!=0)
            if refresh:eligible &= ~border_mask
            candidates=np.where(eligible)[0]
            costs=score_m0[best_m0[candidates],candidates]/np.maximum(1,lengths[methods[candidates]])
            for t in candidates[np.argsort(costs,kind='stable')]:
                if size<=cap:break
                change(t,0)
        if size>cap:
            raise ValueError(f'Presupuesto de fotograma imposible: necesita {size} bytes y dispone de {cap}.')
    used2=np.where(refs2>0)[0];used3=np.where(refs3>0)[0]
    lut=np.zeros(256,dtype=np.uint8);lut[used2]=np.arange(len(used2),dtype=np.uint8)
    m2[methods==2]=lut[m2[methods==2]]
    lut=np.zeros(256,dtype=np.uint8);lut[used3]=np.arange(len(used3),dtype=np.uint8)
    m3[methods==3]=lut[m3[methods==3]]
    return cb32[used2],cb16[used3],size


@njit(cache=False,nogil=True)
def pack_output(pals,tpals,methods,tinds,m2,m3,cb32,cb16):
    pal=np.zeros(pals.shape[0]*32,np.uint8)
    for p in range(pals.shape[0]):
        for c in range(1,16):
            pal[p*32+c*2]=pals[p,c,2]*2
            pal[p*32+c*2+1]=(pals[p,c,1]*32)|(pals[p,c,0]*2)
    pmap=np.zeros(192,np.uint8)
    length=8+cb32.shape[0]*4+cb16.shape[0]*2+192
    for t in range(768):
        if methods[t]==1:length+=32
        elif methods[t]==2:length+=8
        elif methods[t]==3:length+=16
    out=np.zeros(length,np.uint8);pos=0
    for book in range(2):
        if book==0:n=cb32.shape[0]*4
        else:n=cb16.shape[0]*2
        out[pos]=(n>>24)&255;out[pos+1]=(n>>16)&255;out[pos+2]=(n>>8)&255;out[pos+3]=n&255;pos+=4
        if book==0:
            for i in range(cb32.shape[0]):
                for j in range(4):out[pos]=(cb32[i,j*2]<<4)|cb32[i,j*2+1];pos+=1
        else:
            for i in range(cb16.shape[0]):
                for j in range(2):out[pos]=(cb16[i,j*2]<<4)|cb16[i,j*2+1];pos+=1
    method_pos=pos;pos+=192
    for t in range(768):
        shift=6-2*(t%4)
        pmap[t//4] |= (tpals[t]&3)<<shift
        out[method_pos+t//4] |= (methods[t]&3)<<shift
        if methods[t]==1:
            for j in range(32):out[pos]=(tinds[t,j*2]<<4)|tinds[t,j*2+1];pos+=1
        elif methods[t]==2:
            for j in range(8):out[pos]=m2[t,j];pos+=1
        elif methods[t]==3:
            for j in range(16):out[pos]=m3[t,j];pos+=1
    return pal,pmap,out


@njit(cache=False,nogil=True)
def cluster_tiles(tiles,ids,active_idx,n_pals):
    n=tiles.shape[0]
    global_hist=np.zeros(512,np.float64)
    means=np.zeros((n,3),np.float32)
    avg=np.zeros(3,np.float32)
    for t in range(n):
        for pix in range(64):
            global_hist[ids[t,pix]]+=1
            for c in range(3):means[t,c]+=np.float32(tiles[t,pix,c])
        for c in range(3):means[t,c]/=np.float32(64);avg[c]+=means[t,c]
    global_hist[0]=0
    for c in range(3):avg[c]/=np.float32(n)
    first=0;best=1.e30
    for t in range(n):
        d=np.float32(0)
        for c in range(3):d+=(means[t,c]-avg[c])**2
        if d<best:best=d;first=t
    ctr=np.empty((n_pals,3),np.float32);ctr[0]=means[first]
    mins=np.empty(n,np.float32)
    for t in range(n):
        d=np.float32(0)
        for c in range(3):d+=(means[t,c]-ctr[0,c])**2
        mins[t]=d
    for p in range(1,n_pals):
        first=0;best=-1.
        for t in range(n):
            if mins[t]>best:best=mins[t];first=t
        ctr[p]=means[first]
        for t in range(n):
            d=np.float32(0)
            for c in range(3):d+=(means[t,c]-ctr[p,c])**2
            mins[t]=min(mins[t],d)
    labels=np.empty(n,np.int64)
    for iteration in range(5):
        for t in range(n):
            best=1.e30;label=0
            for p in range(n_pals):
                d=np.float32(0)
                for c in range(3):d+=(means[t,c]-ctr[p,c])**2
                if d<best:best=d;label=p
            labels[t]=label
        for p in range(n_pals):
            accum=np.zeros(3,np.float32);count=0
            for t in range(n):
                if labels[t]==p:
                    count+=1
                    for c in range(3):accum[c]+=means[t,c]
            if count:
                for c in range(3):ctr[p,c]=accum[c]/np.float32(count)
    luma=np.empty(n_pals,np.float32);order=np.arange(n_pals)
    for p in range(n_pals):luma[p]=ctr[p,0]*np.float32(.299)+ctr[p,1]*np.float32(.587)+ctr[p,2]*np.float32(.114)
    for p in range(1,n_pals):
        q=p
        while q>0 and luma[order[q]]<luma[order[q-1]]:
            order[q],order[q-1]=order[q-1],order[q];q-=1
    ordered=ctr.copy()
    for p in range(n_pals):ctr[p]=ordered[order[p]]
    for t in range(n):
        best=1.e30;label=0
        for p in range(n_pals):
            d=np.float32(0)
            for c in range(3):d+=(means[t,c]-ctr[p,c])**2
            if d<best:best=d;label=p
        labels[t]=label
    pals=np.zeros((n_pals,16,3),np.int32)
    for p in range(n_pals):
        hist=np.zeros(512,np.float64);count=0
        for t in range(n):
            if labels[t]==p:
                count+=1
                for pix in range(64):hist[ids[t,pix]]+=1
        if count==0:
            for t in range(n):
                for pix in range(64):hist[ids[t,pix]]+=1
        for c in range(512):hist[c]+=.06*global_hist[c]
        hist[0]=0;pals[p]=palette_counts(hist)
    errors=np.empty((n_pals,512),np.int32)
    for iteration in range(2):
        for p in range(n_pals):
            for col in range(512):
                r=(col>>6)&7;g=(col>>3)&7;b=col&7;best=100000
                for k in range(16):
                    d=(r-pals[p,k,0])**2+(g-pals[p,k,1])**2+(b-pals[p,k,2])**2
                    best=min(best,d)
                errors[p,col]=best
        tile_errors=np.zeros((n_pals,n),np.int32)
        for t in range(n):
            best=10000000;label=0
            for p in range(n_pals):
                e=0
                for pix in range(64):e+=errors[p,ids[t,pix]]
                tile_errors[p,t]=e
                if e<best:best=e;label=p
            labels[t]=label
        grid=np.full(768,-1,np.int32)
        for t in range(n):grid[active_idx[t]]=labels[t]
        for t in range(n):
            cell=active_idx[t];y=cell//32;x=cell%32;best=1.e30;label=0
            for p in range(n_pals):
                neighbors=0
                if y>0 and grid[cell-32]==p:neighbors+=1
                if y<23 and grid[cell+32]==p:neighbors+=1
                if x>0 and grid[cell-1]==p:neighbors+=1
                if x<31 and grid[cell+1]==p:neighbors+=1
                score=tile_errors[p,t]-2.5*neighbors
                if score<best:best=score;label=p
            labels[t]=label
        if iteration==0:
            for p in range(n_pals):
                hist=np.zeros(512,np.float64);count=0
                for t in range(n):
                    if labels[t]==p:
                        count+=1
                        for pix in range(64):hist[ids[t,pix]]+=1
                if count:
                    for c in range(512):hist[c]+=.06*global_hist[c]
                    hist[0]=0;pals[p]=palette_counts(hist)
    lut=np.empty((n_pals,512),np.uint8)
    for p in range(n_pals):
        for col in range(512):
            r=(col>>6)&7;g=(col>>3)&7;b=col&7;best=100000;label=0
            for k in range(16):
                d=(r-pals[p,k,0])**2+(g-pals[p,k,1])**2+(b-pals[p,k,2])**2
                if d<best:best=d;label=k
            lut[p,col]=label
    return pals,labels,lut
