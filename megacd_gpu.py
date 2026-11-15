# -*- coding: utf-8 -*-
"""Cálculo Cinepak en CUDA para cualquier contenedor, con respaldo CPU.

No utiliza NVENC: sus formatos H.264/HEVC/AV1 no sirven para Mega CD.
Los kernels comparan vectores RGB VDP y los dos bancos de imagen.
"""
import ctypes as C
import glob
import os
from pathlib import Path
import sys
import threading
import numpy as np

_enabled=True
_backend=None
_failure=''
_dll_directories=[]

SOURCE=r'''
extern "C" __global__ void nearest(const float* queries,const float* book,unsigned char* output,int ncodes,int dim) {
    int q=blockIdx.x, lane=threadIdx.x, best=2147483647, index=0;
    for(int k=lane;k<ncodes;k+=blockDim.x) {
        int score=0;
        for(int j=0;j<dim;j++) { int d=(int)queries[q*dim+j]-(int)book[k*dim+j];score+=d*d; }
        if(score<best || (score==best && k<index)) {best=score;index=k;}
    }
    __shared__ int scores[128];__shared__ int indexes[128];
    scores[lane]=best;indexes[lane]=index;__syncthreads();
    for(int stride=64;stride;stride/=2) {
        if(lane<stride) {
            int s=scores[lane+stride],i=indexes[lane+stride];
            if(s<scores[lane] || (s==scores[lane] && i<indexes[lane])) {scores[lane]=s;indexes[lane]=i;}
        }
        __syncthreads();
    }
    if(lane==0) output[q]=(unsigned char)indexes[0];
}
extern "C" __global__ void previous_bank(const float* target,const unsigned char* prior,const float* palettes,
                                         float* mean,float* maximum,float* scores,int npals) {
    int t=blockIdx.x,pix=threadIdx.x;
    __shared__ int sums[64];__shared__ int maxs[64];
    int idx=prior[t*64+pix];
    for(int p=0;p<npals;p++) {
        int d0=(int)target[t*192+pix*3]-(int)palettes[p*48+idx*3];
        int d1=(int)target[t*192+pix*3+1]-(int)palettes[p*48+idx*3+1];
        int d2=(int)target[t*192+pix*3+2]-(int)palettes[p*48+idx*3+2];
        int value=d0*d0+d1*d1+d2*d2;
        sums[pix]=value;maxs[pix]=value;__syncthreads();
        for(int stride=32;stride;stride/=2) {
            if(pix<stride) {sums[pix]+=sums[pix+stride];if(maxs[pix+stride]>maxs[pix])maxs[pix]=maxs[pix+stride];}
            __syncthreads();
        }
        if(pix==0) {int o=p*768+t;mean[o]=(float)sums[0]/64.f;maximum[o]=(float)maxs[0];scores[o]=mean[o]+0.15f*maximum[o];}
        __syncthreads();
    }
}
'''

def configure(mode):
    global _enabled
    _enabled=mode not in ('cpu','off') and os.environ.get('MEGACD_DISABLE_CUDA')!='1'

def _nvrtc_path():
    locations=[Path(getattr(sys,'_MEIPASS',Path(__file__).parent))]
    try:
        import nvidia.cuda_nvrtc
        locations += [Path(p)/'bin' for p in nvidia.cuda_nvrtc.__path__]
    except ImportError:pass
    for root in locations:
        matches=sorted(root.glob('nvrtc64_*.dll'))
        if matches:return matches[-1]
    raise OSError('No se encuentra el compilador CUDA incluido.')

class CUDA:
    def __init__(self):
        if os.name!='nt':raise OSError('Este paquete CUDA está preparado para Windows.')
        path=_nvrtc_path()
        _dll_directories.append(os.add_dll_directory(str(path.parent)))
        self.builtins=[C.WinDLL(str(p)) for p in path.parent.glob('nvrtc-builtins64_*.dll')]
        os.environ['PATH']=str(path.parent)+os.pathsep+os.environ.get('PATH','')
        self.driver=C.WinDLL('nvcuda.dll');self.compiler=C.WinDLL(str(path))
        self.pool={};self.lock=threading.RLock();self.calls=0;self.previous_calls=0;self.vectors=0
        self._declare()
        self.check(self.driver.cuInit(0))
        device=C.c_int();self.check(self.driver.cuDeviceGet(C.byref(device),0))
        name=C.create_string_buffer(256);self.check(self.driver.cuDeviceGetName(name,256,device));self.name=name.value.decode()
        major=C.c_int();minor=C.c_int()
        self.check(self.driver.cuDeviceComputeCapability(C.byref(major),C.byref(minor),device))
        self.context=C.c_void_p();self.check(self.driver.cuCtxCreate_v2(C.byref(self.context),0,device))
        program=C.c_void_p()
        self.nvcheck(self.compiler.nvrtcCreateProgram(C.byref(program),SOURCE.encode(),b'megacd.cu',0,None,None))
        try:
            options=[f'--gpu-architecture=compute_{major.value}{minor.value}'.encode(),b'--std=c++11',b'--fmad=false']
            arguments=(C.c_char_p*len(options))(*options)
            error=self.compiler.nvrtcCompileProgram(program,len(options),arguments)
            if error:
                size=C.c_size_t();self.compiler.nvrtcGetProgramLogSize(program,C.byref(size))
                log=C.create_string_buffer(size.value);self.compiler.nvrtcGetProgramLog(program,log)
                raise RuntimeError('CUDA: '+log.value.decode('utf-8','replace'))
            size=C.c_size_t();self.nvcheck(self.compiler.nvrtcGetPTXSize(program,C.byref(size)))
            ptx=C.create_string_buffer(size.value);self.nvcheck(self.compiler.nvrtcGetPTX(program,ptx))
            self.module=C.c_void_p();self.check(self.driver.cuModuleLoadData(C.byref(self.module),ptx))
        finally:self.compiler.nvrtcDestroyProgram(C.byref(program))
        self.nearest_function=C.c_void_p();self.previous_function=C.c_void_p()
        self.check(self.driver.cuModuleGetFunction(C.byref(self.nearest_function),self.module,b'nearest'))
        self.check(self.driver.cuModuleGetFunction(C.byref(self.previous_function),self.module,b'previous_bank'))
    def _declare(self):
        v=C.c_void_p;i=C.c_int;u=C.c_uint;z=C.c_size_t;d=C.c_uint64
        signatures={'cuInit':[u],'cuDeviceGet':[C.POINTER(i),i],'cuDeviceGetName':[v,i,i],
            'cuDeviceComputeCapability':[C.POINTER(i),C.POINTER(i),i],
            'cuCtxCreate_v2':[C.POINTER(v),u,i],'cuCtxSetCurrent':[v],
            'cuMemAlloc_v2':[C.POINTER(d),z],'cuMemFree_v2':[d],
            'cuMemcpyHtoD_v2':[d,v,z],'cuMemcpyDtoH_v2':[v,d,z],
            'cuModuleLoadData':[C.POINTER(v),v],'cuModuleGetFunction':[C.POINTER(v),v,C.c_char_p],
            'cuLaunchKernel':[v,u,u,u,u,u,u,u,v,C.POINTER(v),C.POINTER(v)]}
        for name,args in signatures.items():
            fn=getattr(self.driver,name);fn.argtypes=args;fn.restype=i
        signatures={'nvrtcCreateProgram':[C.POINTER(v),C.c_char_p,C.c_char_p,i,v,v],
                    'nvrtcCompileProgram':[v,i,C.POINTER(C.c_char_p)],
                    'nvrtcGetProgramLogSize':[v,C.POINTER(z)],'nvrtcGetProgramLog':[v,v],
                    'nvrtcGetPTXSize':[v,C.POINTER(z)],'nvrtcGetPTX':[v,v],'nvrtcDestroyProgram':[C.POINTER(v)]}
        for name,args in signatures.items():
            fn=getattr(self.compiler,name);fn.argtypes=args;fn.restype=i
    @staticmethod
    def check(code):
        if code:raise RuntimeError(f'CUDA Driver: código {code}.')
    @staticmethod
    def nvcheck(code):
        if code:raise RuntimeError(f'Compilador CUDA: código {code}.')
    def buffer(self,name,size):
        current=self.pool.get(name)
        if current is None or current[1]<size:
            if current:self.check(self.driver.cuMemFree_v2(current[0]))
            pointer=C.c_uint64();self.check(self.driver.cuMemAlloc_v2(C.byref(pointer),max(1,size)))
            current=(pointer.value,size);self.pool[name]=current
        return current[0]
    def upload(self,name,array):
        arr=np.ascontiguousarray(array)
        pointer=self.buffer(name,arr.nbytes)
        self.check(self.driver.cuMemcpyHtoD_v2(pointer,C.c_void_p(arr.ctypes.data),arr.nbytes))
        return pointer
    def launch(self,fn,grid,block,parameters):
        arguments=(C.c_void_p*len(parameters))(*(C.addressof(p) for p in parameters))
        self.check(self.driver.cuLaunchKernel(fn,grid,1,1,block,1,1,0,None,arguments,None))
    def nearest(self,queries,book):
        with self.lock:
            self.check(self.driver.cuCtxSetCurrent(self.context))
            q=np.ascontiguousarray(queries,dtype=np.float32);b=np.ascontiguousarray(book,dtype=np.float32)
            out=np.empty(q.shape[0],np.uint8)
            qp=self.upload('query',q);bp=self.upload('book',b);op=self.buffer('nearest_output',out.nbytes)
            self.launch(self.nearest_function,len(out),128,[C.c_uint64(qp),C.c_uint64(bp),C.c_uint64(op),C.c_int(len(b)),C.c_int(q.shape[1])])
            self.check(self.driver.cuMemcpyDtoH_v2(C.c_void_p(out.ctypes.data),op,out.nbytes))
            self.calls+=1;self.vectors+=len(out)
            return out
    def previous(self,target,prior,palettes):
        with self.lock:
            self.check(self.driver.cuCtxSetCurrent(self.context))
            target=np.ascontiguousarray(target,dtype=np.float32);prior=np.ascontiguousarray(prior,dtype=np.uint8)
            palettes=np.ascontiguousarray(palettes,dtype=np.float32)
            shape=(len(palettes),768);mean=np.empty(shape,np.float32);maximum=np.empty(shape,np.float32);scores=np.empty(shape,np.float32)
            pointers=[self.upload('target_rgb',target),self.upload('prior_indices',prior),self.upload('palettes',palettes)]
            for name in ('mean','maximum','scores'):pointers.append(self.buffer(name,mean.nbytes))
            self.launch(self.previous_function,768,64,[*(C.c_uint64(p) for p in pointers),C.c_int(len(palettes))])
            for array,pointer in zip((mean,maximum,scores),pointers[3:]):
                self.check(self.driver.cuMemcpyDtoH_v2(C.c_void_p(array.ctypes.data),pointer,array.nbytes))
            self.previous_calls+=1
            return mean,maximum,scores

def get_backend():
    global _backend,_failure
    if not _enabled or _failure:return None
    if _backend is None:
        try:_backend=CUDA()
        except Exception as error:_failure=str(error)
    return _backend

def nearest(queries,book):
    global _failure
    backend=get_backend()
    if backend is None:return None
    try:return backend.nearest(queries,book)
    except Exception as error:_failure=str(error);return None

def previous(target,prior,palettes):
    global _failure
    backend=get_backend()
    if backend is None:return None
    try:return backend.previous(target,prior,palettes)
    except Exception as error:_failure=str(error);return None

def stats():
    if _backend is None:return {'available':False,'reason':_failure,'nearest_calls':0,'previous_calls':0,'vectors':0}
    return {'available':not bool(_failure),'reason':_failure,'device':_backend.name,'nearest_calls':_backend.calls,
            'previous_calls':_backend.previous_calls,'vectors':_backend.vectors}
