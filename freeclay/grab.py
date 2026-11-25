"""Absolute Grab through a moving deformation field with a symmetric seam.

NumPy is shipped with Blender; no external package or Python runtime is used.
"""
import math
import numpy as np


class GrabDeformation:
    def __init__(self,mesh,matrix,anchor,radius,symmetry):
        self.mesh=mesh
        self.inverse=matrix.inverted()
        self.linear=np.array(matrix.to_3x3(),dtype=np.float64)
        self.original=np.empty(len(mesh.vertices)*3,dtype=np.float64)
        mesh.vertices.foreach_get('co',self.original)
        self.original=self.original.reshape((-1,3))
        self.anchor=np.array(self.inverse@anchor,dtype=np.float64)
        self.radius=max(radius,1e-8)
        self.symmetry=symmetry
        self.mobility=np.ones(len(mesh.vertices),dtype=np.float64)
        mask=mesh.attributes.get('.sculpt_mask')
        if mask:
            mask.data.foreach_get('value',self.mobility)
            self.mobility=np.clip(1-self.mobility,0,1)**2
        hidden={i for f in mesh.polygons if f.hide for i in f.vertices}
        for v in mesh.vertices:
            if v.hide or v.index in hidden:self.mobility[v.index]=0
        self.sides=np.where(self.original[:,0]*self.anchor[0]<0,-1.,1.)

    def _field(self,positions,center,delta):
        offset=positions-center
        if self.symmetry:
            offset[:,0]=positions[:,0]-center[0]*self.sides
        world=offset@self.linear.T
        r2=np.einsum('ij,ij->i',world,world)/(self.radius*self.radius)
        weight=np.maximum(0,1-r2)**2*self.mobility
        movement=weight[:,None]*delta
        if self.symmetry:
            # Integrate a continuous X velocity that vanishes on the mirror plane.
            # Do not clamp vertices onto the plane after they cross it.
            seam=np.minimum(1,np.abs(positions[:,0])/abs(center[0])) if abs(center[0])>1e-10 else 0
            movement[:,0]*=self.sides*seam
        return movement

    def apply(self,world_delta,strength):
        delta=np.array(self.inverse.to_3x3()@world_delta)*min(1,max(0,strength))
        target=self.anchor+delta
        log_ratio=0.
        if self.symmetry:
            ax=self.anchor[0]
            if abs(ax)<1e-10:
                target[0]=ax
            else:
                signed=delta[0]*math.copysign(1,ax)
                # Inward drags compress continuously, even if the cursor crosses
                # the mirror plane. The two sides retain their separation.
                target[0]=ax/(1-signed/abs(ax)) if signed<0 else ax+delta[0]
                log_ratio=math.log(target[0]/ax)
        speed=np.abs(target-self.anchor)
        if self.symmetry and log_ratio:
            speed[0]=max(abs(self.anchor[0]),abs(target[0]))*abs(log_ratio)
        world_speed=np.sum(np.linalg.norm(self.linear,axis=0)*speed)
        steps=max(1,math.ceil(world_speed/self.radius/.1),math.ceil(abs(log_ratio)/.1))
        positions=self.original.copy()
        center=self.anchor.copy()
        for step in range(1,steps+1):
            following=self.anchor+(target-self.anchor)*(step/steps)
            if self.symmetry and log_ratio:
                following[0]=self.anchor[0]*math.exp(log_ratio*step/steps)
            displacement=following-center
            midpoint=positions+.5*self._field(positions,center,displacement)
            positions+=self._field(midpoint,(center+following)*.5,displacement)
            center=following
        # Recompute from the stroke start: no event-rate dependence or accumulated
        # drift, and returning the pointer restores the original coordinates.
        self.mesh.vertices.foreach_set('co',positions.astype(np.float32).ravel())
        self.mesh.update()
