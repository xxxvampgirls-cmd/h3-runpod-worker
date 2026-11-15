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

class FreeVideoSafetyTests(unittest.TestCase):
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

if __name__=="__main__":
    unittest.main()
