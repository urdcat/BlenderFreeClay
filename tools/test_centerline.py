"""Geometric Grab regression on owned Blender meshes; no external Python runtime."""
import sys,pathlib,json,traceback,time,zipfile,types,hashlib
import bpy,bmesh
from mathutils import Vector,Matrix
from mathutils.bvhtree import BVHTree
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from freeclay.grab import GrabDeformation
import numpy as np
if '--previous' in sys.argv:
    with zipfile.ZipFile(ROOT/'dist'/'freeclay-0.1.1.zip') as archive:
        previous=types.ModuleType('previous_grab')
        exec(compile(archive.read('grab.py'),'freeclay-0.1.1.zip/grab.py','exec'),previous.__dict__)
        GrabDeformation=previous.GrabDeformation
report={'version':bpy.app.version_string,'implementation':'0.1.1' if '--previous' in sys.argv else 'current',
        'numpy':{'version':np.__version__,'path':np.__file__,'init_sha256':hashlib.sha256(pathlib.Path(np.__file__).read_bytes()).hexdigest()},'checks':[]}
try:
    for kind in ('plane','sphere'):
        for anchor_x,delta in ((0,(0,1,0)),(.025,(0,1,0)),(.025,(-.15,.7,0)),(.025,(.15,.7,0)),(-.025,(.15,.7,0))):
            mesh=bpy.data.meshes.new('Centerline regression')
            bm=bmesh.new()
            if kind=='plane':
                bmesh.ops.create_grid(bm,x_segments=80,y_segments=80,size=1)
            else:
                bmesh.ops.create_icosphere(bm,subdivisions=6,radius=1)
                bmesh.ops.symmetrize(bm,input=list(bm.verts)+list(bm.edges)+list(bm.faces),direction='-X',dist=1e-6)
            bmesh.ops.triangulate(bm,faces=list(bm.faces));bm.to_mesh(mesh);bm.free()
            target=Vector((anchor_x,0,0 if kind=='plane' else 1))
            candidates=(v for v in mesh.vertices if abs(v.co.x)<1e-7) if anchor_x==0 else (v for v in mesh.vertices if v.co.x*anchor_x>0 and abs(v.co.x)>.005)
            vertex=min(candidates,key=lambda v:(v.co-target).length_squared)
            anchor=vertex.co.copy();original=[v.co.copy() for v in mesh.vertices]
            grab=GrabDeformation(mesh,Matrix.Identity(4),anchor,.4,True)
            start=time.perf_counter();grab.apply(Vector(delta),1);elapsed=time.perf_counter()-start
            bm=bmesh.new();bm.from_mesh(mesh);bm.normal_update()
            degenerate=sum(f.calc_area()<1e-12 for f in bm.faces)
            flipped=sum(f.normal.z<0 for f in bm.faces) if kind=='plane' else None
            collapsed=sum(abs(p.x)>1e-7 and abs(v.co.x)<1e-9 for v,p in zip(mesh.vertices,original))
            tree=BVHTree.FromBMesh(bm)
            triangles=[set(f.vertices) for f in mesh.polygons]
            crossings=sum(a<b and not triangles[a].intersection(triangles[b]) for a,b in tree.overlap(tree))
            report['checks'].append(dict(kind=kind,anchor_x=anchor.x,delta=delta,degenerate=degenerate,
                flipped=flipped,collapsed_to_seam=collapsed,nonadjacent_intersections=crossings,seconds=elapsed))
            bm.free()
            grab.apply(Vector((0,0,0)),1)
            assert all((v.co-p).length<1e-7 for v,p in zip(mesh.vertices,original))
    report['passed']=all(c['degenerate']==0 and c['collapsed_to_seam']==0 and not c['flipped'] and c['nonadjacent_intersections']==0 for c in report['checks'])
except Exception:
    report['passed']=False;report['error']=traceback.format_exc()
destination=pathlib.Path(sys.argv[-1]) if '--' in sys.argv else ROOT/'verification'/'centerline.json'
destination.write_text(json.dumps(report,indent=2),encoding='utf8')
print(json.dumps(report))
if not report['passed']:raise RuntimeError('Centerline geometry regression')
