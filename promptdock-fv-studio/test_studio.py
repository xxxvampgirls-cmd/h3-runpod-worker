import json
import os
import time
import tempfile
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from studio import extract_loras, read_lora_details, from_freevideo, validate_deletion_folder
from cleanup import inspect, eligible_failed
from bridge_discovery import normalize_custom_nodes, discover_custom_nodes
from bridge_payloads import BRIDGE_INIT, BRIDGE_JS

class FreeVideoSafetyTests(unittest.TestCase):
    def test_embedded_bridge_payload(self):
        from bridge_embedded import BRIDGE_JS, BRIDGE_PY
        self.assertIn("PromptDock one-way local handoff",BRIDGE_PY)
        self.assertIn("PromptDock.FreeVideoBridge",BRIDGE_JS)
        self.assertIn("/promptdock-bridge/pending",BRIDGE_JS)
        self.assertIn("/promptdock-bridge/ack",BRIDGE_PY)
        self.assertGreater(len(BRIDGE_JS),2500)

    def test_detect_custom_nodes_from_output_root(self):
        from integration import nodes_from_choice,discover
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/"FreeVideo-engine"/"ComfyUI"
            nodes=root/"custom_nodes"
            nodes.mkdir(parents=True)
            output=root/"output"
            (output/"FreeVideo").mkdir(parents=True)
            matches=[str(p) for p in nodes_from_choice(output)]
            self.assertIn(str(nodes),matches)
            scanned=discover(output=str(output),limit_seconds=2,max_entries=400)
            self.assertIn(str(nodes),scanned)

    def test_cleanup_only_counts_finished_reclaimable_work(self):
        from cleanup import inspect
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder)/"output"
            run=base/"FreeVideo"/"2026-11-15"/("c"*32)
            work=run/"video.artifacts"
            work.mkdir(parents=True)
            (run/"video.mp4").write_bytes(b"video")
            (run/"comfy-request.json").write_text(json.dumps({"status":"complete"}))
            (run/"video.request.json").write_text(json.dumps({"success":True,"preview":False}))
            (work/"latents.pt").write_bytes(b"x"*2048)
            (work/"rgb.npy").write_bytes(b"y"*1024)
            info=inspect(str(base))
            self.assertEqual(info["working_estimate"],3072)
            self.assertEqual(info["video_bytes"],5)
            (run/"video.request.json").write_text(json.dumps({"success":True,"preview":True}))
            info=inspect(str(base))
            self.assertEqual(info["working_estimate"],1024)

    def test_originals_link_only_to_explicit_saved_assets(self):
        from reference_links import linked_originals
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/"ComfyUI"
            output=root/"output"/"FreeVideo"
            output.mkdir(parents=True)
            source=root/"input"/"freevideo"/("a"*32)/"original_photo.jpg"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"JPG")
            assets=[{"file":f"freevideo/{'a'*32}/original_photo.jpg","role":"first"}]
            request={"assets":json.dumps(assets)}
            linked=linked_originals(output,request)
            self.assertEqual(len(linked),1)
            self.assertEqual(linked[0]["path"],str(source))
            self.assertTrue(linked[0]["exists"])
            self.assertEqual(linked[0]["role"],"first")
            self.assertFalse((root/"output"/"FreeVideo"/"original_photo.jpg").exists())
            self.assertEqual(linked_originals(output,{"prompt":"original_photo.jpg"}),[])
            source.unlink()
            missing=linked_originals(output,request)
            self.assertEqual(len(missing),1)
            self.assertFalse(missing[0]["exists"])
            self.assertEqual(linked_originals(output,
                {"assets":[{"file":"../../other.jpg","role":"first"}]}),[])

    def test_failed_intermediate_markers_are_reported(self):
        from cleanup import recovery_evidence
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)
            self.assertEqual(recovery_evidence(run),[])
            (run/"first.png").write_bytes(b"frame")
            (run/"video.artifacts").mkdir()
            (run/"video.artifacts"/"latents.pt").write_bytes(b"latents")
            indicators=recovery_evidence(run)
            self.assertIn("first.png",indicators)
            self.assertIn("video.artifacts/latents.pt",indicators)
            self.assertTrue((run/"first.png").is_file())

    def test_own_player_class_can_be_imported_without_automatic_launch(self):
        from studio_player import VideoPlayer
        self.assertEqual(VideoPlayer._format_time(125),"02:05")

    def test_saved_reference_images_preserved_and_detected(self):
        from studio import run_reference_images,pics
        with tempfile.TemporaryDirectory() as directory:
            run=Path(directory)
            for name in ("first.png","last.png","reference-00.png","reference-01.png"):
                (run/name).write_bytes(b"sample-image")
            (run/"reference-02.mp4").write_bytes(b"sample-video")
            (run/"video.mp4").write_bytes(b"mp4")
            refs=run_reference_images(run)
            self.assertEqual(len(refs),4)
            self.assertEqual([Path(p).name for p in refs],
                ["first.png","last.png","reference-00.png","reference-01.png"])
            self.assertEqual(len(pics({"sourceImages":refs})),4)

    def test_media_reads_all_five_with_strength(self):
        rows=[{"path":f"C:\\ComfyUI\\models\\loras\\lora-{i}.safetensors","strength":(i+1)*0.25}
              for i in range(5)]
        parsed=extract_loras({"version":1,"loras":rows},"Применена")
        self.assertEqual(len(parsed),5)
        self.assertEqual([x["strength"] for x in parsed],[.25,.5,.75,1.,1.25])
        self.assertEqual(parsed[0]["name"],"lora-0.safetensors")

    def test_workflow_stack_json_and_disabled(self):
        adapters=json.dumps([{"name":"a.safetensors","strength":.7,"enabled":True},
                             {"name":"b.safetensors","strength":.4,"enabled":True},
                             {"name":"c.safetensors","strength":2.,"enabled":False},
                             {"name":"d.safetensors","strength":0.0,"enabled":True}])
        workflow={"nodes":[{"type":"FreeVideoLoRAStack","widgets_values":[adapters]}]}
        found=extract_loras(workflow,"В настройках")
        self.assertEqual(len(found),2)
        self.assertEqual([x["name"] for x in found],["a.safetensors","b.safetensors"])
        self.assertEqual([x["strength"] for x in found],[.7,.4])

    def test_read_actual_source_and_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            run=Path(d)
            (run/"media.json").write_text(json.dumps({"loras":[
                {"path":"C:/loras/used.safetensors","strength":1.5},
                {"path":"C:/loras/used2.safetensors","strength":.35}]}))
            wf={"nodes":[{"type":"FreeVideoLoRAStack","widgets_values":[
                json.dumps([{"name":"only-planned.safetensors","strength":.9}])]}]}
            found=read_lora_details(run,{},wf)
            self.assertEqual([x["source"] for x in found],["Применена","Применена","В настройках"])
            self.assertEqual(found[0]["strength"],1.5)

    def test_safeguard_and_full_scan(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"output"/"FreeVideo"
            folder=root/"2026-11-15"/("a"*32)
            folder.mkdir(parents=True)
            video=folder/"video.mp4"
            video.write_bytes(b"fake-video")
            (folder/"prompt.txt").write_text("cinematic wolf")
            (folder/"media.json").write_text(json.dumps({"loras":[
                {"path":f"D:/loras/lora_{i}.safetensors","strength":i/10+.5} for i in range(5)]}))
            (folder/"comfy-request.json").write_text(json.dumps({"status":"complete","seed":123}))
            rows=from_freevideo(str(root.parent))
            self.assertEqual(len(rows),1)
            self.assertEqual(len(rows[0]["loraDetails"]),5)
            path,size=validate_deletion_folder(rows[0],str(root.parent))
            self.assertEqual(path,folder)
            self.assertGreater(size,0)
            (folder/"comfy-request.json").write_text(json.dumps({"status":"running"}))
            with self.assertRaises(ValueError):
                validate_deletion_folder(rows[0],str(root.parent))

    def test_disk_audit_is_readonly_and_respects_preview(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"output"/"FreeVideo"
            done=root/"2026-11-15"/("b"*32)
            fail=root/"2026-11-14"/("c"*32)
            done.mkdir(parents=True)
            fail.mkdir(parents=True)
            (done/"video.mp4").write_bytes(b"mp4"+b"x"*100)
            (done/"comfy-request.json").write_text(json.dumps({"status":"complete"}))
            (done/"video.request.json").write_text(json.dumps({"success":True}))
            art=done/"video.artifacts";art.mkdir()
            (art/"rgb.npy").write_bytes(b"r"*12_000)
            (art/"latents.pt").write_bytes(b"l"*44_000)
            (fail/"prompt.txt").write_text("bad take")
            record=fail/"comfy-request.json"
            record.write_text(json.dumps({"status":"failed"}))
            before=time.time()-48*3600
            for p in [fail/"prompt.txt",record]:os.utime(p,(before,before))
            stats=inspect(root.parent,now=time.time())
            self.assertEqual(stats["videos"],1)
            self.assertEqual(stats["failed_count"],1)
            self.assertEqual(stats["working_estimate"],56_000)
            self.assertTrue((art/"latents.pt").is_file())
            self.assertGreater(eligible_failed(fail,root.parent),0)
            (fail/"video.mp4").write_bytes(b"mp4")
            with self.assertRaises(ValueError):eligible_failed(fail,root.parent)
            (done/"video.request.json").write_text(json.dumps({"success":True,"preview":True}))
            stats=inspect(root.parent,now=time.time())
            self.assertEqual(stats["working_estimate"],12_000)
            self.assertTrue((done/"video.mp4").exists())

    def test_failed_cleanup_rejects_unknown_content(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/"FreeVideo"
            fail=root/"2026-11-14"/("d"*32)
            fail.mkdir(parents=True)
            record=fail/"comfy-request.json"
            record.write_text(json.dumps({"status":"cancelled"}))
            user_note=fail/"notes-by-user.txt"
            user_note.write_text("keep")
            before=time.time()-40*3600
            for p in [record,user_note]:os.utime(p,(before,before))
            with self.assertRaises(ValueError):eligible_failed(fail,root)
            self.assertTrue(user_note.exists())


    def test_embedded_bridge_resources_match_sources(self):
        here = Path(__file__).resolve().parent / "bridge"
        self.assertIn("PromptDock one-way local handoff", BRIDGE_INIT)
        self.assertIn("PromptDock.FreeVideoBridge", BRIDGE_JS)
        self.assertEqual(BRIDGE_INIT, (here/"__init__.py").read_text(encoding="utf-8"))
        self.assertEqual(BRIDGE_JS, (here/"web"/"bridge.js").read_text(encoding="utf-8"))

    def test_discover_freevideo_and_manual_folder(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            installed = root/"FreeVideo-engine"/"ComfyUI"/"custom_nodes"
            (installed/"FreeVideo").mkdir(parents=True)
            (root/"my_models").mkdir()
            choices = discover_custom_nodes(scan_roots=[root])
            self.assertIn(installed, choices)
            self.assertEqual(choices[0], installed)
            self.assertEqual(normalize_custom_nodes(root/"FreeVideo-engine"), installed)
            self.assertEqual(normalize_custom_nodes(root/"FreeVideo-engine"/"ComfyUI"), installed)
            self.assertEqual(normalize_custom_nodes(installed), installed)
            self.assertIsNone(normalize_custom_nodes(root/"my_models"))

    def test_discovery_does_not_follow_linked_install(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            actual = root/"actual"/"custom_nodes"
            actual.mkdir(parents=True)
            linked = root/"FreeVideo-engine"
            try:
                linked.symlink_to(root/"actual", target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("Directory symlinks are unavailable on this platform")
            self.assertNotIn(linked/"custom_nodes", discover_custom_nodes(scan_roots=[root]))

if __name__=="__main__":
    unittest.main()
