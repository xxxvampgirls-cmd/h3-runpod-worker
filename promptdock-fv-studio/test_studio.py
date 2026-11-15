import json
import tempfile
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from studio import extract_loras, read_lora_details, from_freevideo, validate_deletion_folder

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
if __name__=="__main__":
    unittest.main()
