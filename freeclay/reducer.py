"""Conservative local endpoint decimation, Blender-bundled modules only.

No smoothing, projection, or repositioning of surviving vertices. Accumulated
plane quadrics rank collapses; sampled surface error and triangle orientation
guard them. Disjoint one-rings are welded together in one native BMesh call.
The reference surface and spatial index are immutable for one stroke.
"""
import math
import time
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree


def quadric_add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def quadric_cost(q, p):
    x, y, z = p
    return max(0.0, q[0]*x*x + 2*q[1]*x*y + 2*q[2]*x*z + 2*q[3]*x
               + q[4]*y*y + 2*q[5]*y*z + 2*q[6]*y + q[7]*z*z
               + 2*q[8]*z + q[9]) / max(q[10], 1e-20)


class Reducer:
    def __init__(self, mesh, matrix_world=None):
        self.bm = bmesh.new()
        self.bm.from_mesh(mesh)
        if not self.bm.verts or not self.bm.faces:
            self.bm.free(); self.bm=None
            raise ValueError('面のあるメッシュを選択してください')
        if any(len(f.verts) != 3 for f in self.bm.faces):
            self.bm.free()
            self.bm = None
            raise ValueError('FreeClay Reduce requires triangles; use Open as Clay (Copy) first')
        self.matrix = matrix_world.copy() if matrix_world is not None else None
        if self.matrix is not None:
            self.bm.transform(self.matrix)
        self.bm.normal_update()
        self.bm.verts.ensure_lookup_table()
        self.reference = BVHTree.FromBMesh(self.bm)
        self.vertices = list(self.bm.verts)
        self.vertex_ids = {v:i for i,v in enumerate(self.vertices)}
        self.parents = list(range(len(self.vertices)))
        self.reference_faces = [tuple(self.vertex_ids[v] for v in f.verts) for f in self.bm.faces]
        self.tree = KDTree(len(self.vertices))
        for i, v in enumerate(self.vertices):
            self.tree.insert(v.co, i)
        self.tree.balance()
        self.quadrics = {}
        self.mask = self.bm.verts.layers.float.get('.sculpt_mask')
        self.face_sets = self.bm.faces.layers.int.get('.sculpt_face_set')
        self.initial_faces = len(self.bm.faces)
        self.total_collapses = 0
        self._mirror_cache = {}
        self._mirror_tolerance = max(1e-7, max((v.co-self.vertices[0].co).length for v in self.vertices)*1e-6)

    def close(self):
        if self.bm is not None:
            self.bm.free()
            self.bm = None

    def raycast(self, origin, direction):
        return self.reference.ray_cast(origin, direction)

    def _representative(self,index):
        root=index
        while self.parents[root]!=root:
            root=self.parents[root]
        while self.parents[index]!=index:
            parent=self.parents[index]; self.parents[index]=root; index=parent
        return self.vertices[root]

    def _mirror_vertex(self,v):
        if v not in self._mirror_cache:
            p=self.matrix.inverted()@v.co if self.matrix is not None else v.co.copy()
            p.x=-p.x
            if self.matrix is not None:
                p=self.matrix@p
            _,index,distance=self.tree.find(p)
            other=self.vertices[index]
            # Native Smooth can introduce small coordinate differences even with
            # X symmetry enabled. Accept only mutually nearest correspondence,
            # bounded by a fraction of the local edge length; never move vertices.
            tolerance=max(self._mirror_tolerance,min((e.calc_length() for e in v.link_edges),default=0)*.15)
            partner=None
            if other.is_valid and distance<=tolerance:
                back=self.matrix.inverted()@other.co if self.matrix is not None else other.co.copy()
                back.x=-back.x
                if self.matrix is not None:
                    back=self.matrix@back
                _,reverse,_=self.tree.find(back)
                if self.vertices[reverse]==v:
                    partner=other
            self._mirror_cache[v]=partner
        partner=self._mirror_cache[v]
        return partner if partner is not None and partner.is_valid else None

    def _quadric(self, v):
        if v not in self.quadrics:
            q = [0.0] * 11
            for f in v.link_faces:
                nx, ny, nz = f.normal
                d = -f.normal.dot(v.co)
                # Area weights prevent densely triangulated sides dominating.
                w = max(f.calc_area(), 1e-16)
                p = (nx*nx,nx*ny,nx*nz,nx*d,ny*ny,ny*nz,ny*d,nz*nz,nz*d,d*d,1)
                q = [a + w*b for a,b in zip(q,p)]
            self.quadrics[v] = tuple(q)
        return self.quadrics[v]

    def _protected(self, v, feature_cos):
        if v.hide or not v.is_manifold or (self.mask and v[self.mask] > 0.001):
            return True
        if any(f.hide for f in v.link_faces):
            return True
        if self.face_sets and len({f[self.face_sets] for f in v.link_faces}) > 1:
            return True
        for e in v.link_edges:
            if e.seam or not e.smooth or not e.is_manifold:
                return True
            if e.link_faces[0].normal.dot(e.link_faces[1].normal) < feature_cos:
                return True
        return False

    def _safe(self, source, target, tolerance):
        """Link condition, normal/area guard, and sampled reference distance."""
        a = {e.other_vert(source) for e in source.link_edges}
        b = {e.other_vert(target) for e in target.link_edges}
        shared = a & b
        edge = next((e for e in source.link_edges if e.other_vert(source) == target), None)
        if edge is None or len(edge.link_faces) != 2:
            return False
        opposite = {v for f in edge.link_faces for v in f.verts if v not in (source,target)}
        if shared != opposite or len(opposite) != 2:
            return False
        # Prevent tetrahedra / duplicate faces, including very small components.
        target_faces = {frozenset(f.verts) for f in target.link_faces}
        new_triangles = []
        for f in source.link_faces:
            if target in f.verts:
                continue
            if len(f.verts) != 3:
                return False
            new_verts = [target if v == source else v for v in f.verts]
            if frozenset(new_verts) in target_faces:
                return False
            p = [v.co for v in new_verts]
            cross = (p[1]-p[0]).cross(p[2]-p[0])
            area2 = cross.length
            if area2 < max(1e-18, f.calc_area()*0.10):
                return False
            if cross.dot(f.normal) < area2*0.85:
                return False
            samples = [(p[0]+p[1]+p[2])/3, (p[0]+p[1])*0.5,
                       (p[1]+p[2])*0.5, (p[2]+p[0])*0.5]
            for sample in samples:
                hit = self.reference.find_nearest(sample, tolerance*1.001)
                if hit[0] is None:
                    return False
            new_triangles.append(p)
        # Reverse check: the removed vertex must still lie near the new fan.
        from mathutils.geometry import closest_point_on_tri
        if not new_triangles or min((closest_point_on_tri(source.co,*p)-source.co).length
                                    for p in new_triangles) > tolerance:
            return False
        return True

    def dab(self, center, radius, view_direction, strength=0.55, preserve=0.8,
            max_collapses=48, budget_ms=22.0, mirror_x=False):
        started = time.perf_counter()
        center, direction = Vector(center), Vector(view_direction).normalized()
        if radius <= 1e-10:
            return {'collapsed':0, 'milliseconds':0.0}
        nearby = [(d,self.vertices[i]) for _,i,d in self.tree.find_range(center,radius)
                  if self.vertices[i].is_valid]
        if not nearby:
            return {'collapsed':0, 'milliseconds':0.0}
        # Surface connectivity keeps a second shell behind the stroke untouched.
        candidates = {v for _,v in nearby if v.normal.dot(-direction) > 0.12 and not v.hide}
        # Follow the hit triangle's surviving vertices. Nearest surviving vertex
        # alone can jump onto a second, very close shell after prior collapses.
        hit=self.reference.find_nearest(center)
        anchors=[self._representative(i) for i in self.reference_faces[hit[2]]] if hit[0] is not None else []
        anchors=[v for v in anchors if v in candidates]
        seed=min(anchors,key=lambda v:(v.co-center).length_squared) if anchors else None
        active, pending = set(), [seed] if seed in candidates else []
        while pending:
            v = pending.pop()
            if v in active:
                continue
            active.add(v)
            pending.extend(e.other_vert(v) for e in v.link_edges
                           if e.other_vert(v) in candidates and e.other_vert(v) not in active)
        feature_cos = math.cos(math.radians(42.0))
        tolerance = radius*(0.001 + 0.012*(1.0-preserve))
        target_length = radius*(0.11+0.23*strength)
        edges = {e for v in active for e in v.link_edges
                 if all(w in active for w in e.verts) and len(e.link_faces)==2
                 and all(len(f.verts)==3 for f in e.link_faces)}
        ranked = []
        for e in edges:
            a,b = e.verts
            distance = max((a.co-center).length,(b.co-center).length)/radius
            falloff = (1-distance*distance)**2
            if falloff < 0.015 or e.calc_length() > target_length*math.sqrt(falloff):
                continue
            q = quadric_add(self._quadric(a),self._quadric(b))
            ca, cb = quadric_cost(q,a.co), quadric_cost(q,b.co)
            cost, target, source = (ca,a,b) if ca<=cb else (cb,b,a)
            local_tol = tolerance*(0.35+0.65*falloff)
            if cost <= local_tol*local_tol:
                # Stable geometry-based tie-breaks make mirror sides reproducible.
                ranked.append((cost/max(falloff,0.01), tuple(sorted((tuple(a.co),tuple(b.co)))),
                               source,target,q,local_tol))
        ranked.sort(key=lambda row:(row[0],row[1]))
        used, weld, updates = set(), {}, {}
        protected={}
        def is_protected(v):
            if v not in protected:
                protected[v]=self._protected(v,feature_cos)
            return protected[v]
        validation_started=time.perf_counter()
        limit = max(1,round(max_collapses*max(0.05,strength)))
        for _,_,source,target,q,tol in ranked:
            if len(weld)>=limit:
                break
            if weld and (time.perf_counter()-validation_started)*1000 > min(budget_ms,12.0):
                break
            if is_protected(source) or is_protected(target):
                continue
            ring = {source,target} | {e.other_vert(v) for v in (source,target) for e in v.link_edges}
            if ring & used or not self._safe(source,target,tol):
                continue
            partners=[]
            if mirror_x:
                ms,mt=self._mirror_vertex(source),self._mirror_vertex(target)
                if ms is None or mt is None:
                    continue
                if ms!=source or mt!=target:
                    mirror_ring={ms,mt}|{e.other_vert(v) for v in (ms,mt) for e in v.link_edges}
                    # Crossing or touching the symmetry seam is kept conservative.
                    if mirror_ring & (used|ring) or is_protected(ms) or is_protected(mt):
                        continue
                    mq=quadric_add(self._quadric(ms),self._quadric(mt))
                    if quadric_cost(mq,mt.co)>tol*tol or not self._safe(ms,mt,tol):
                        continue
                    partners=[(ms,mt,mq)]
                    ring.update(mirror_ring)
            weld[source] = target
            updates[target] = q
            for ms,mt,mq in partners:
                weld[ms]=mt; updates[mt]=mq
            used.update(ring)
        if weld:
            # Freeze all original planes that this batch is about to change,
            # including neighboring vertices not selected as collapse endpoints.
            for v in used:
                self._quadric(v)
            for source,target in weld.items():
                self.parents[self.vertex_ids[source]]=self.vertex_ids[target]
            bmesh.ops.weld_verts(self.bm,targetmap=weld)
            self.quadrics.update(updates)
            for v in weld:
                self.quadrics.pop(v,None)
            self.bm.normal_update()
        self.total_collapses += len(weld)
        return {'collapsed':len(weld), 'triangulated':0,
                'milliseconds':(time.perf_counter()-started)*1000,
                'candidates':len(ranked)}

    def write_mesh(self, mesh):
        self.bm.to_mesh(mesh)
        if self.matrix is not None:
            mesh.transform(self.matrix.inverted())
        mesh.update()
