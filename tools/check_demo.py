"""Independent-process reload validation, no writes to the loaded scene."""
import bpy,sys,pathlib,json,bmesh
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import freeclay
freeclay.register()
obj=bpy.context.object
assert obj is not None and obj.type=='MESH' and obj.get('freeclay_enabled')
if obj.use_dynamic_topology_sculpting:
    bpy.ops.sculpt.dynamic_topology_toggle('EXEC_DEFAULT',False)
bm=bmesh.new(); bm.from_mesh(obj.data)
assert all(e.is_manifold for e in bm.edges)
assert len(bm.faces)>100
result={'passed':True,'version':bpy.app.version_string,'file':bpy.data.filepath,
        'object':obj.name,'faces':len(bm.faces),'manifold':True,'x_symmetry':obj.data.use_mirror_x,
        'freeclay_symmetry':bpy.context.scene.freeclay.symmetry}
bm.free()
(ROOT/'verification'/'demo-reload.json').write_text(json.dumps(result,indent=2),encoding='utf8')
print(json.dumps(result))
freeclay.unregister()
