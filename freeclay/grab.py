"""Absolute Grab through a moving deformation field with a symmetric seam.

NumPy is shipped with Blender; no external package or Python runtime is used.
"""
import math
import numpy as np
import bmesh


def mirror_brush_joined(matrix,local_anchor,radius):
    """At least half a diameter overlaps: reflected centers <= one radius."""
    return 2*abs(local_anchor[0])*matrix.to_3x3().col[0].length<=radius


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
        # Fix this choice for the stroke so sideways drags cannot switch fields.
        self.joined=symmetry and mirror_brush_joined(matrix,self.anchor,self.radius)
        self.mobility=np.ones(len(mesh.vertices),dtype=np.float64)
        mask=mesh.attributes.get('.sculpt_mask')
        if mask:
            mask.data.foreach_get('value',self.mobility)
            self.mobility=np.clip(1-self.mobility,0,1)**2
        hidden={i for f in mesh.polygons if f.hide for i in f.vertices}
        for v in mesh.vertices:
            if v.hide or v.index in hidden:self.mobility[v.index]=0
        self.sides=np.where(self.original[:,0]*self.anchor[0]<0,-1.,1.)
        self._rest=None
        self._refined=False
        self._last_delta=None
        self.edges=np.empty(len(mesh.edges)*2,dtype=np.int32)
        mesh.edges.foreach_get('vertices',self.edges);self.edges=self.edges.reshape((-1,2))
        lengths=np.linalg.norm((self.original[self.edges[:,0]]-self.original[self.edges[:,1]])@self.linear.T,axis=1)
        self.edge_limit=max(self.radius*.16,float(np.median(lengths))*1.5) if len(lengths) else self.radius*.16

    def _field(self,positions,center,delta,mobility,sides):
        offset=positions-center
        if self.symmetry:
            if self.joined:
                # A capsule around the segment between the two brush centers.
                # The shared strip has no |x|-shaped valley or doubled strength.
                offset[:,0]=np.sign(positions[:,0])*np.maximum(0,np.abs(positions[:,0])-abs(center[0]))
            else:offset[:,0]=positions[:,0]-center[0]*sides
        world=offset@self.linear.T
        r2=np.einsum('ij,ij->i',world,world)/(self.radius*self.radius)
        weight=np.maximum(0,1-r2)**2*mobility
        movement=weight[:,None]*delta
        if self.symmetry:
            # Integrate a continuous X velocity that vanishes on the mirror plane.
            # Do not clamp vertices onto the plane after they cross it.
            seam=np.minimum(1,np.abs(positions[:,0])/abs(center[0])) if abs(center[0])>1e-10 else 0
            movement[:,0]*=sides*seam
        return movement

    def _path(self,delta):
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
        center=self.anchor.copy()
        for step in range(1,steps+1):
            following=self.anchor+(target-self.anchor)*(step/steps)
            if self.symmetry and log_ratio:
                following[0]=self.anchor[0]*math.exp(log_ratio*step/steps)
            yield center,following
            center=following

    def _positions(self,original,mobility,delta):
        positions=original.copy()
        sides=np.where(original[:,0]*self.anchor[0]<0,-1.,1.)
        for center,following in self._path(delta):
            displacement=following-center
            midpoint=positions+.5*self._field(positions,center,displacement,mobility,sides)
            positions+=self._field(midpoint,(center+following)*.5,displacement,mobility,sides)
        return positions

    def apply(self,world_delta,strength):
        delta=np.array(self.inverse.to_3x3()@world_delta)*min(1,max(0,strength))
        if self._last_delta is not None and np.array_equal(delta,self._last_delta):return
        positions=self._positions(self.original,self.mobility,delta)
        edge_vectors=(positions[self.edges[:,0]]-positions[self.edges[:,1]])@self.linear.T
        rest_vectors=(self.original[self.edges[:,0]]-self.original[self.edges[:,1]])@self.linear.T
        stretched=np.linalg.norm(edge_vectors,axis=1)>np.maximum(self.edge_limit,np.linalg.norm(rest_vectors,axis=1)*1.8)
        if np.any(stretched):
            self._refine(positions,delta)
            self._last_delta=delta.copy()
            return
        if self._refined:
            self._rest.to_mesh(self.mesh)
            self._refined=False
        # Recompute from the stroke start: no event-rate dependence or accumulated
        # drift, and returning the pointer restores the original coordinates.
        self.mesh.vertices.foreach_set('co',positions.astype(np.float32).ravel())
        self.mesh.update()
        self._last_delta=delta.copy()

    def _refine(self,positions,delta):
        # Replay from the original mesh, inserting samples DURING the flow before
        # long triangles lose the surface curvature. Splitting only at the end
        # would retain the folds; subdividing rest-space repeatedly loses float
        # precision near points stretched by many brush radii.
        if self._rest is None:
            self._rest=bmesh.new();self._rest.from_mesh(self.mesh)
            for v,p in zip(self._rest.verts,self.original):v.co=p
        bm=self._rest.copy()
        try:
            mobility_layer=bm.verts.layers.float.new('__freeclay_grab_mobility')
            for v,w in zip(bm.verts,self.mobility):v[mobility_layer]=w
            positions=self.original.copy();mobility=self.mobility.copy()
            sides=self.sides.copy()
            bm.verts.index_update();bm.edges.index_update()
            edges=list(bm.edges)
            ids=np.array([[e.verts[0].index,e.verts[1].index] for e in edges])
            for center,following in self._path(delta):
                displacement=following-center
                before=np.linalg.norm((positions[ids[:,0]]-positions[ids[:,1]])@self.linear.T,axis=1)
                midpoint=positions+.5*self._field(positions,center,displacement,mobility,sides)
                positions+=self._field(midpoint,(center+following)*.5,displacement,mobility,sides)
                length=np.linalg.norm((positions[ids[:,0]]-positions[ids[:,1]])@self.linear.T,axis=1)
                candidates=np.flatnonzero((np.round(length,6)>self.edge_limit)&(length>before+1e-6))
                selected=[edges[i] for i in candidates if not edges[i].hide]
                if selected and len(bm.verts)<len(self.original)+50000:
                    # Match the split order on mirrored triangles so subsequent
                    # diagonals and inserted samples also have mirrored partners.
                    keys=np.round(positions,5);keys[:,0]=np.abs(keys[:,0])
                    def edge_key(edge):
                        return tuple(sorted(tuple(keys[v.index]) for v in edge.verts))
                    selected.sort(key=edge_key)
                    new_positions=[];new_mobility=[];touched=set()
                    # Split each triangle explicitly. A batch edge subdivision
                    # can leave collinear quad corners which a subsequent beauty
                    # triangulation connects into zero-area or duplicate faces.
                    for edge in selected:
                        a,b=edge.verts
                        a.co=positions[a.index];b.co=positions[b.index]
                        point=(positions[a.index]+positions[b.index])*.5
                        weight=(mobility[a.index]+mobility[b.index])*.5
                        adjacent=[(f,next(v for v in f.verts if v not in edge.verts)) for f in edge.link_faces]
                        _,middle=bmesh.utils.edge_split(edge,edge.verts[0],.5)
                        middle.index=len(positions)+len(new_positions)
                        middle[mobility_layer]=weight
                        new_positions.append(point);new_mobility.append(weight)
                        for face,opposite in adjacent:bmesh.utils.face_split(face,middle,opposite)
                        touched.update(middle.link_edges)
                        touched.add(edge)
                    new_ids=[]
                    for edge in touched:
                        pair=[edge.verts[0].index,edge.verts[1].index]
                        if edge.index<0:
                            edge.index=len(edges);edges.append(edge);new_ids.append(pair)
                        else:ids[edge.index]=pair
                    if new_ids:ids=np.concatenate((ids,np.array(new_ids)),axis=0)
                    positions=np.concatenate((positions,np.array(new_positions)),axis=0)
                    mobility=np.concatenate((mobility,np.array(new_mobility)))
                    sides=np.where(positions[:,0]*self.anchor[0]<0,-1.,1.)
            for v,p in zip(bm.verts,positions):v.co=p
            bm.verts.layers.float.remove(mobility_layer)
            bm.to_mesh(self.mesh);self.mesh.update();self._refined=True
        finally:bm.free()

    def close(self):
        if getattr(self,'_rest',None) is not None:
            self._rest.free();self._rest=None

    def __del__(self):self.close()
