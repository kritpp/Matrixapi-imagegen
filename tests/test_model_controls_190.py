import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/Matrixapi-imagegen/scripts'))
import generate


class ModelControlTests(unittest.TestCase):
    def test_every_alias_anywhere_with_legacy_and_skill_prefix(self):
        for alias, expected in generate.MODEL_SHORTCUTS.items():
            for prefix in ('-', '模型-', '技能-', '模型—'):
                marker = prefix + alias
                for prompt in (marker+'画花海', '生成'+marker+'一张花海', '生成花海，'+marker, '生成花海\n'+marker+'\n明亮'):
                    with self.subTest(prompt=prompt):
                        model, text=generate.select_model_from_prompt('gpt-image-2',prompt,explicit_model=True)
                        self.assertEqual(model,expected)
                        self.assertNotIn(alias,text)
                        self.assertIn('花海',text)

    def test_similar_names_and_urls_do_not_match(self):
        for text in ('-m8fast','-m8t2','-f2.60','abc-m8','https://example.com/-m8','file-m8.png','-s2.6abc'):
            self.assertIsNone(generate.prompt_model_marker(text),text)
        self.assertEqual(generate.prompt_model_marker('花海-M8F'),'midjourney-v8.2-fast')

    def test_conflict_fails_locally(self):
        with self.assertRaises(generate.ImageGenError):
            generate.prompt_model_marker('花海 -m8 -m8f')
        self.assertEqual(generate.prompt_model_marker('-m8 花海 模型-m8'),'midjourney-v8.2')

    def test_new_models_route_discovery(self):
        for model in (generate.FLARE_26_IMAGE_MODEL,generate.SUNBURST_26_IMAGE_MODEL,generate.MIDJOURNEY_MODELS[0]):
            self.assertEqual(generate.select_model_for_available_routes('gpt-image-2',{model},explicit_selection=False),model)

    def test_mj_group_defaults_standard_and_fast_requires_explicit_selection(self):
        for configured in ('gpt-image-2',*generate.MIDJOURNEY_MODELS):
            self.assertEqual(generate.select_model_for_available_routes(configured,set(generate.MIDJOURNEY_MODELS),explicit_selection=False),'midjourney-v8.2')
        for fast in generate.MIDJOURNEY_MODELS[1:]:
            with self.assertRaises(generate.ImageGenError):
                generate.select_model_for_available_routes('gpt-image-2',{fast},explicit_selection=False)
            self.assertEqual(generate.select_model_for_available_routes(fast,set(generate.MIDJOURNEY_MODELS),explicit_selection=True),fast)

    def test_mj_ratio_translation(self):
        for prompt in ('花海 比例16：9','16:9 花海','花海 --ar 16：9','花海 --aspect 16:9'):
            text,ratio=generate.prepare_midjourney_prompt(prompt)
            self.assertEqual(ratio,'16:9')
            self.assertTrue(text.endswith('--ar 16:9'))
            self.assertEqual(text.count('--ar'),1)
        text,ratio=generate.prepare_midjourney_prompt('花海 --hd --s 250','9：16')
        self.assertEqual(ratio,'9:16')
        self.assertIn('--hd --s 250',text)
        self.assertTrue(text.endswith('--ar 9:16'))

    def test_fast_turbo_cannot_leak_from_configuration(self):
        for configured in generate.MIDJOURNEY_MODELS[1:]:
            for available in (None,set(generate.SUPPORTED_MODELS)):
                self.assertEqual(generate.select_model_for_available_routes(configured,available,explicit_selection=False),'midjourney-v8.2')
                self.assertEqual(generate.select_model_for_available_routes(configured,available,explicit_selection=True),configured)

    def test_mj_conflicting_or_invalid_ratio(self):
        for prompt,ratio in [('花海 --ar 9:16','16:9'),('比例4:3 花海16:9','auto'),('花海 --ar nope','auto')]:
            with self.assertRaises(generate.ImageGenError):generate.prepare_midjourney_prompt(prompt,ratio)

    def test_mj_body_only_supported_fields_and_no_retry(self):
        for model in generate.MIDJOURNEY_MODELS:
            with mock.patch.object(generate,'_post_image_request',return_value={'data':[]}) as post:
                generate.call_api('https://matrixapii.com/v1/images/generations','key',model,'花海','native',4,60,{'quality':'auto'},aspect_ratio='16：9')
            body=json.loads(post.call_args.args[2])
            self.assertEqual(set(body),{'model','prompt'})
            self.assertEqual(body['model'],model)
            self.assertTrue(body['prompt'].endswith('--ar 16:9'))
            self.assertEqual(post.call_count,1)
        with mock.patch.object(generate,'_post_image_request',side_effect=generate.ImageGenError('gateway',status_code=502)) as post:
            with self.assertRaises(generate.ImageGenError):generate.call_api('endpoint','key',generate.MIDJOURNEY_MODELS[0],'cup','native',4,60)
            self.assertEqual(post.call_count,1)

    def run_main(self,extra):
        png=b'\x89PNG\r\n\x1a\n'+b'\x00\x00\x00\rIHDR'+(1536).to_bytes(4,'big')+(864).to_bytes(4,'big')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            argv=['generate.py','--prompt','生成 -m8 花海 比例16：9','--task-id','task-control-190-0001','--out-dir',str(root/'images'),*extra]
            stdout,stderr=io.StringIO(),io.StringIO()
            with mock.patch.object(sys,'argv',argv),mock.patch.object(generate.Path,'home',return_value=root/'home'),mock.patch.object(generate,'discover_credentials',return_value=('https://matrixapii.com','key','gpt-image-2','test')),mock.patch.object(generate,'_post_image_request',return_value={'data':[{'b64_json':base64.b64encode(png).decode()}]*4}) as post,mock.patch.object(generate,'_schedule_result_cleanup',return_value=True),mock.patch.object(sys,'stdout',stdout),mock.patch.object(sys,'stderr',stderr):
                code=generate.main()
            result=json.loads(stdout.getvalue()) if stdout.getvalue().strip() else {}
            if code==0:
                self.assertTrue(all(Path(p).is_file() for p in result['download_files']))
            return code,result,stderr.getvalue(),post.call_count

    def test_mj_all_four_saved_with_real_dimensions(self):
        code,result,err,calls=self.run_main([])
        self.assertEqual(code,0,err)
        self.assertEqual(calls,1)
        self.assertEqual(result['count'],4)
        self.assertEqual(result['requested_size'],'native')
        self.assertEqual([x.replace('×','x') for x in result['actual_sizes']],['1536x864']*4)
        self.assertEqual(result['actual_model'],'midjourney-v8.2')

    def test_mj_unsupported_requests_never_submit(self):
        for extra in (['--size','4K'],['--n','2'],['--async'],['--quality','high'],['--reference-url','https://example.com/a.png']):
            with self.subTest(extra=extra):
                code,result,err,calls=self.run_main(extra)
                self.assertNotEqual(code,0)
                self.assertEqual(calls,0)


if __name__=='__main__':unittest.main()
