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
            self.assertEqual(Path(linked[0]["path"]).resolve(),source.resolve())
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

    def test_failed_retry_artifacts_require_warning(self):
        from cleanup import recovery_evidence
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            retry=root/"video.artifacts"/"attempts"/"01-retry"
            retry.mkdir(parents=True)
            (retry/"latents.pt").write_bytes(b"latent-data")
            findings=recovery_evidence(root)
            self.assertTrue(any("attempts/01-retry/latents.pt" in p for p in findings))

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

    def test_lora_cache_only_int8_convrot_and_hardlinks_not_source(self):
        from lora_cache_cleaner import scan_parent, inspect_variant, recycle_one
        with tempfile.TemporaryDirectory() as root:
            cache=Path(root)/"int8-convrot"
            cache.mkdir()
            folder=cache/("vdn-online-lora-"+"b"*24)
            (folder/"blocks").mkdir(parents=True)
            (folder/"lora").mkdir()
            source=Path(root)/"model-source"
            source.write_bytes(b"shared base model weight")
            os.link(source,folder/"blocks"/"00.safetensors")
            adapter=folder/"lora"/"00.safetensors"
            adapter.write_bytes(b"unique lora factors")
            manifest=folder/"manifest.json"
            manifest.write_text(json.dumps({"source_id":"b"*64,
                "groups":[{"file":"blocks/00.safetensors"},
                          {"file":"lora/00.safetensors"}],
                "online_lora":{"version":1},
                "lora_identity":{"adapters":[{"name":"skating.safetensors","strength":0.7}]}}))
            old=time.time()-40*3600
            for p in (folder/"blocks"/"00.safetensors",adapter,manifest):
                os.utime(p,(old,old))
            result=scan_parent(cache)
            self.assertEqual(len(result["rows"]),1)
            self.assertEqual(result["rows"][0]["hardlinked"],len(source.read_bytes()))
            self.assertEqual(result["rows"][0]["reclaimable"],
                             len(adapter.read_bytes())+manifest.stat().st_size)
            self.assertTrue(result["rows"][0]["safe"])
            captures=[]
            recycle_one(folder,cache,captures.append)
            self.assertEqual(captures,[str(folder)])
            self.assertTrue(source.is_file())
            self.assertTrue(adapter.is_file(),"Test captures call without mutating files")
            (folder/"user-notes.txt").write_text("don't touch")
            with self.assertRaises(ValueError):
                recycle_one(folder,cache,captures.append)
            self.assertEqual(len(captures),1)

    def test_one_click_lora_cleanup_checks_all_and_preserves_models(self):
        from lora_cache_cleaner import scan_parent, recycle_all_safe
        import shutil
        with tempfile.TemporaryDirectory() as root:
            root=Path(root)
            cache=root/"int8-convrot"
            cache.mkdir()
            model=root/"h3-model.safetensors"
            model.write_bytes(b"model bytes")
            old=time.time()-3*86400
            for letter in "abc":
                folder=cache/("vdn-online-lora-"+letter*24)
                (folder/"lora").mkdir(parents=True)
                if letter=="c":
                    (folder/"unknown-data.txt").write_text("protect")
                    continue
                (folder/"lora"/"root.safetensors").write_bytes(b"adapter")
                os.link(model,folder/"00.safetensors")
                (folder/"manifest.json").write_text(json.dumps({
                    "source_id":letter*64,
                    "groups":[{"file":"00.safetensors"},
                              {"file":"lora/root.safetensors"}],
                    "lora_identity":{"adapters":[]},
                    "online_lora":{"version":1}}))
                for p in folder.rglob("*"):
                    if p.is_file():os.utime(p,(old,old))
            trash=root/"recycle-bin"
            trash.mkdir()
            def simulated_trash(value):
                shutil.move(value,trash/Path(value).name)
            result=recycle_all_safe(cache,simulated_trash)
            self.assertEqual(len(result["moved"]),2)
            self.assertEqual(len(result["remaining"]["skipped"]),1)
            self.assertTrue(model.exists())
            self.assertTrue((cache/("vdn-online-lora-"+"c"*24)).is_dir())
            self.assertEqual(len(list(trash.iterdir())),2)

    def test_cleaner_window_one_click_starts_from_explicit_open(self):
        from lora_cache_ui import LoraCacheWindow
        code=(Path(__file__).parent/"lora_cache_ui.py").read_text(encoding="utf-8")
        self.assertIn("self.after(180,self.begin_one_click)",code)
        self.assertIn("self.locate(then_cleanup=True)",code)
        self.assertIn("self.auto_cleanup()",code)

    def test_lora_cache_protects_recent_variants(self):
        from lora_cache_cleaner import inspect_variant, recycle_one
        with tempfile.TemporaryDirectory() as root:
            cache=Path(root)/"int8-convrot"
            cache.mkdir()
            folder=cache/("vdn-online-lora-"+"f"*24)
            folder.mkdir()
            (folder/"manifest.json").write_text(json.dumps({
                "source_id":"f"*64,"groups":[],
                "online_lora":{},"lora_identity":{"adapters":[]}}))
            row=inspect_variant(folder,cache)
            self.assertFalse(row["safe"])
            sent=[]
            with self.assertRaises(ValueError):
                recycle_one(folder,cache,sent.append)
            self.assertEqual(sent,[])

    def test_manual_only_no_scheduled_disk_refresh(self):
        code=(Path(__file__).parent/"studio.py").read_text(encoding="utf-8")
        self.assertNotIn("self.after(120000,self.auto_scan)",code)
        self.assertNotIn("self.after(30000,self.periodic_storage_audit)",code)
        self.assertNotIn("self.after(400,self.scan)",code)
        self.assertIn("self.open_lora_cache",code)

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
