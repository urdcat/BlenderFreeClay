"""Joined Grab and seam-band reduction on owned Blender fixtures."""
import bpy,bmesh,sys,pathlib,json,math,types,zipfile,traceback
from mathutils import Vector,Matrix
from mathutils.kdtree import KDTree
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from freeclay.grab import GrabDeformation
from freeclay.reducer import Reducer
previous='--previous' in sys.argv
if previous:
    with zipfile.ZipFile(ROOT/'dist/freeclay-0.1.6.zip') as archive:
        modules={}
        for name in ('grab','reducer'):
            module=types.ModuleType('old_'+name);exec(compile(archive.read(name+'.py'),'old/'+name+'.py','exec'),module.__dict__);modules[name]=module
        GrabDeformation=modules['grab'].GrabDeformation;Reducer=modules['reducer'].Reducer
report={'previous':previous,'checks':[]}
try:
    mesh=bpy.data.meshes.new('Seam Grab');bm=bmesh.new()
    bmesh.ops.create_grid(bm,x_segments=80,y_segments=80,size=1)
    bmesh.ops.triangulate(bm,faces=list(bm.faces));bmesh.ops.symmetrize(bm,input=list(bm.verts)+list(bm.edges)+list(bm.faces),direction='-X',dist=1e-8)
    bm.to_mesh(mesh);bm.free()
    origin=[v.co.copy() for v in mesh.vertices]
    grab=GrabDeformation(mesh,Matrix.Identity(4),Vector((.2,0,0)),1,True)
    grab.apply(Vector((0,0,.4)),1)
    center=min(mesh.vertices,key=lambda v:origin[v.index].length_squared)
    neighbor=min(mesh.vertices,key=lambda v:(origin[v.index]-Vector((.025,0,0))).length_squared)
    cusp=abs(neighbor.co.z-center.co.z)/abs(neighbor.co.x)
    anchor=min(mesh.vertices,key=lambda v:(origin[v.index]-Vector((.2,0,0))).length_squared)
    anchor_height=anchor.co.z
    report['checks'].append({'joined_grab_seam_slope':cusp,'center_height':center.co.z,'anchor_height':anchor.co.z})
    if not previous:
        # The threshold is measured in world units; unrelated model scaling
        # must not silently change which side of it a stroke occupies.
        from freeclay.grab import mirror_brush_joined
        assert mirror_brush_joined(Matrix.Identity(4),Vector((.5,0,0)),1)
        assert not mirror_brush_joined(Matrix.Identity(4),Vector((.501,0,0)),1)
        assert mirror_brush_joined(Matrix.Diagonal((2,1,1,1)),Vector((.25,0,0)),1)
        assert not mirror_brush_joined(Matrix.Diagonal((2,1,1,1)),Vector((.251,0,0)),1)
    grab.close();bpy.data.meshes.remove(mesh)
    mesh=bpy.data.meshes.new('Dense flat seam');bm=bmesh.new()
    bmesh.ops.create_grid(bm,x_segments=60,y_segments=50,size=1)
    for v in bm.verts:v.co.x=math.copysign(abs(v.co.x)**3,v.co.x)
    bmesh.ops.triangulate(bm,faces=list(bm.faces));bmesh.ops.symmetrize(bm,input=list(bm.verts)+list(bm.edges)+list(bm.faces),direction='-X',dist=1e-8)
    bm.to_mesh(mesh);bm.free()
    initial={tuple(v.co) for v in mesh.vertices}
    def band():return sum(abs(v.co.x)<.002 and abs(v.co.y)<.3 for v in mesh.vertices)
    before=band();faces=len(mesh.polygons)
    reducer=Reducer(mesh)
    results=[]
    for i in range(60):results.append(reducer.dab((0,.04*math.sin(i),0),.65,(0,0,-1),1,.8,max_collapses=128,budget_ms=100,mirror_x=True))
    reducer.write_mesh(mesh);reducer.close()
    after=band();bm=bmesh.new();bm.from_mesh(mesh)
    degenerate=sum(f.calc_area()<1e-14 for f in bm.faces)
    bad_edges=sum(not e.is_manifold and not e.is_boundary for e in bm.edges)
    bm.free()
    kd=KDTree(len(mesh.vertices))
    for v in mesh.vertices:kd.insert(v.co,v.index)
    kd.balance();mirror=max(kd.find(Vector((-v.co.x,v.co.y,v.co.z)))[2] for v in mesh.vertices)
    unchanged=all(tuple(v.co) in initial for v in mesh.vertices)
    report['checks'].append({'band_before':before,'band_after':after,'faces_before':faces,'faces_after':len(mesh.polygons),'degenerate':degenerate,'nonmanifold_edges':bad_edges,'mirror_error':mirror,'survivor_positions_unchanged':unchanged})
    report['passed']=cusp<.001 and abs(anchor_height-.4)<1e-6 and after<before*.2 and degenerate==bad_edges==0 and mirror<1e-6 and unchanged
    if not previous:
        # Protect a masked strip through the same shared-neighbor path.
        protected={tuple(v.co) for v in mesh.vertices if abs(v.co.x)<.08 and abs(v.co.y)<.3}
        attr=mesh.attributes.new('.sculpt_mask','FLOAT','POINT')
        for v in mesh.vertices:attr.data[v.index].value=float(tuple(v.co) in protected)
        reducer=Reducer(mesh)
        for i in range(10):reducer.dab((.04,.02*math.sin(i),0),.65,(0,0,-1),1,.8,max_collapses=128,budget_ms=100,mirror_x=True)
        reducer.write_mesh(mesh);reducer.close()
        assert protected<={tuple(v.co) for v in mesh.vertices}
        report['checks'].append({'shared_seam_mask_protection':True,'world_scale_threshold':True})
except Exception:report['passed']=False;report['error']=traceback.format_exc()
(ROOT/'verification'/('seam-previous.json' if previous else 'seam.json')).write_text(json.dumps(report,indent=2),encoding='utf8');print(json.dumps(report))
if not previous and not report['passed']:raise RuntimeError('Seam regression')
