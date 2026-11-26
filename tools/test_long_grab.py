"""Long-stroke surface regression on disposable Blender meshes."""
import bpy,bmesh,sys,pathlib,json,time,types,zipfile,traceback
import numpy as np
from mathutils import Matrix,Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from freeclay.grab import GrabDeformation
previous='--previous' in sys.argv
if previous:
    with zipfile.ZipFile(ROOT/'dist/freeclay-0.1.5.zip') as archive:
        module=types.ModuleType('previous_grab');exec(compile(archive.read('grab.py'),'previous/grab.py','exec'),module.__dict__)
        GrabDeformation=module.GrabDeformation
report={'previous':previous,'checks':[]}
try:
    for case,(x,delta,radius,symmetry) in enumerate([(0,(0,3,0),.65,True),(.35,(0,3,0),.65,True),(.35,(-.6,3,0),.65,True),(.35,(2,3,0),.65,True),(.35,(0,4,0),.65,False),(.35,(0,2,0),.35,False),(.35,(0,3,0),.65,False)]):
        mesh=bpy.data.meshes.new('Long Grab fixture');bm=bmesh.new()
        bmesh.ops.create_icosphere(bm,subdivisions=5,radius=1.5)
        bmesh.ops.symmetrize(bm,input=list(bm.verts)+list(bm.edges)+list(bm.faces),direction='-X',dist=1e-6)
        bmesh.ops.triangulate(bm,faces=list(bm.faces));bm.to_mesh(mesh);bm.free()
        vertex=min((v for v in mesh.vertices if not symmetry or (abs(v.co.x)<1e-7 if x==0 else v.co.x>0)),key=lambda v:(v.co-Vector((x,0,1.5))).length_squared)
        anchor=vertex.co.copy();anchor_id=vertex.index;before=len(mesh.polygons)
        original=[tuple(v.co) for v in mesh.vertices]
        if case==6:
            mask=mesh.attributes.new('.sculpt_mask','FLOAT','POINT')
            for v in mesh.vertices:mask.data[v.index].value=1 if v.co.x<.1 else 0
        grab=GrabDeformation(mesh,Matrix.Identity(4),anchor,radius,symmetry)
        start=time.perf_counter();grab.apply(Vector(delta),1);elapsed=time.perf_counter()-start
        polygons=[tuple(f.vertices) for f in mesh.polygons];tree=BVHTree.FromPolygons([v.co for v in mesh.vertices],polygons)
        crossings=sum(i<j and not set(polygons[i]).intersection(polygons[j]) for i,j in tree.overlap(tree))
        bm=bmesh.new();bm.from_mesh(mesh)
        max_edge=max(e.calc_length() for e in bm.edges)
        degenerate=sum(f.calc_area()<1e-12 for f in bm.faces)
        manifold=all(e.is_manifold for e in bm.edges);bm.free()
        target=anchor+Vector(delta)
        # Inward X movement uses the existing seam-preserving compression.
        if symmetry and x==0:target.x=anchor.x
        if symmetry and delta[0]<0:target.x=anchor.x/(1-delta[0]/anchor.x)
        error=(mesh.vertices[anchor_id].co-target).length
        if case==6:
            assert max((mesh.vertices[i].co-Vector(p)).length for i,p in enumerate(original) if p[0]<.1)<1e-7
            assert all(mesh.attributes['.sculpt_mask'].data[i].value==1 for i,p in enumerate(original) if p[0]<.1)
        mirror=0
        if symmetry:
            kd=KDTree(len(mesh.vertices))
            for v in mesh.vertices:kd.insert(v.co,v.index)
            kd.balance()
            mirror=max(kd.find(Vector((-v.co.x,v.co.y,v.co.z)))[2] for v in mesh.vertices)
        check=dict(x=x,delta=delta,radius=radius,symmetry=symmetry,faces_before=before,faces_after=len(mesh.polygons),crossings=crossings,degenerate=degenerate,manifold=manifold,max_edge=max_edge,tracking_error=error,mirror_error=mirror,seconds=elapsed)
        report['checks'].append(check)
        grab.apply(Vector(),1)
        assert [tuple(v.co) for v in mesh.vertices]==original,'Returning the pointer must restore the original mesh'
        if case==4 and not previous:
            grab.apply(Vector(delta),1);grab.close()
            second=GrabDeformation(mesh,Matrix.Identity(4),mesh.vertices[anchor_id].co.copy(),radius,False)
            second.apply(Vector((1,1,0)),1)
            polygons=[tuple(f.vertices) for f in mesh.polygons];tree=BVHTree.FromPolygons([v.co for v in mesh.vertices],polygons)
            assert not any(i<j and not set(polygons[i]).intersection(polygons[j]) for i,j in tree.overlap(tree))
            bm=bmesh.new();bm.from_mesh(mesh)
            assert all(e.is_manifold for e in bm.edges) and all(f.calc_area()>1e-12 for f in bm.faces)
            bm.free();second.close();check['repeated_long_stroke']=True
        if hasattr(grab,'close'):grab.close()
        bpy.data.meshes.remove(mesh)
    report['passed']=all(c['crossings']==0 and c['degenerate']==0 and c['manifold'] and c['tracking_error']<1e-4 and c['max_edge']<.4 and c['mirror_error']<1e-5 for c in report['checks'])
except Exception:report['passed']=False;report['error']=traceback.format_exc()
destination=ROOT/'verification'/('long-grab-previous.json' if previous else 'long-grab.json')
destination.write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report))
if not previous and not report['passed']:raise RuntimeError('Long Grab regression')
