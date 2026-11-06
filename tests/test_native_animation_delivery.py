import argparse
import base64
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/Matrixapi-imagegen/scripts'))
import generate
from animation import inspect_gif
from PIL import Image


def gif_bytes(frames=3, duration=200):
    images = [Image.new('RGB', (24, 24), (i*23 % 256, 255-i*13 % 256, i*59 % 256)) for i in range(frames)]
    output = io.BytesIO()
    images[0].save(output, format='GIF', save_all=True, append_images=images[1:], duration=duration, loop=0)
    return output.getvalue()


class NativeAnimationTests(unittest.TestCase):
    def args(self, extra=()):
        with mock.patch.object(sys, 'argv', ['generate.py','--prompt','摇摆的蓝色钟摆 -f2.6',*extra]):
            return generate.parse_args()

    def test_legacy_controls_remain_serializable_for_existing_task_recovery(self):
        self.assertEqual(generate.native_output_options(self.args(), generate.FLARE_26_IMAGE_MODEL), {})
        args=self.args(['--api-output-format','gif','--frame-count','4','--frame-duration-ms','200','--complexity','low'])
        options=generate.native_output_options(args,generate.FLARE_26_IMAGE_MODEL)
        with mock.patch.object(generate,'_post_image_request',return_value={'data':[]}) as post:
            generate.call_api('endpoint','test-key',generate.FLARE_26_IMAGE_MODEL,'swinging pendulum','896x896',1,30,options)
        body=json.loads(post.call_args.args[2])
        self.assertEqual(body['output_format'],'gif')
        self.assertEqual(body['frame_count'],4)
        self.assertEqual(body['frame_duration_ms'],200)
        self.assertEqual(body['complexity'],'low')
        self.assertNotIn('output_compression',body)
        self.assertEqual(post.call_count,1)

    def test_default_gif_body_omits_frame_controls(self):
        args=self.args(['--api-output-format','gif'])
        options=generate.native_output_options(args,generate.FLARE_26_IMAGE_MODEL)
        with mock.patch.object(generate,'_post_image_request',return_value={'data':[]}) as post:
            generate.call_api('endpoint','test-key',generate.FLARE_26_IMAGE_MODEL,'swinging pendulum','896x896',1,30,options)
        body=json.loads(post.call_args.args[2])
        self.assertEqual(body['output_format'],'gif')
        self.assertNotIn('frame_count',body)
        self.assertNotIn('frame_duration_ms',body)

    def test_invalid_controls_fail_before_submit(self):
        for extra in (['--frame-count','4'], ['--api-output-format','gif','--frame-count','17'], ['--api-output-format','gif','--frame-duration-ms','10'], ['--api-output-format','gif','--output-size','1920x1080'], ['--api-output-format','gif','--output-format','png']):
            with self.subTest(extra=extra), self.assertRaises(generate.ImageGenError):
                generate.native_output_options(self.args(extra),generate.FLARE_26_IMAGE_MODEL)
        for model in (generate.FLARE_IMAGE_MODEL,generate.MIDJOURNEY_MODELS[0],generate.GEMINI_IMAGE_MODEL):
            with self.assertRaises(generate.ImageGenError):
                generate.native_output_options(self.args(['--api-output-format','gif']),model)

    def test_gif_bytes_and_frames_are_preserved(self):
        raw=gif_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            files=generate.save_images({'data':[{'b64_json':base64.b64encode(raw).decode()}]},'https://matrixapii.com/v1/images/generations','test-key',Path(tmp),30,task_id='task-animation',task_fingerprint='a'*64,expected_count=1)
            self.assertEqual(Path(files[0]).suffix,'.gif')
            self.assertEqual(Path(files[0]).read_bytes(),raw)
            meta=inspect_gif(files[0])
            self.assertTrue(meta['animated'])
            self.assertEqual(meta['frame_count'],3)
            self.assertEqual(meta['frame_durations_ms'],[200]*3)
            self.assertEqual(meta['loop'],0)
            result={'ok':True,'api_output_format':'gif','files':files,'display_summary':'24×24'}
            generate.apply_media_metadata(result)
            self.assertTrue(result['requested_output_satisfied'])

    def test_single_frame_or_truncated_gif_is_never_animation_success(self):
        for raw in (gif_bytes(1),gif_bytes()[:-4]):
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'image.gif';path.write_bytes(raw)
                result={'ok':True,'api_output_format':'gif','files':[str(path)]}
                generate.apply_media_metadata(result)
                self.assertFalse(result['ok'])
                self.assertFalse(result['requested_output_satisfied'])
                self.assertEqual(path.read_bytes(),raw)

    def test_upstream_frame_control_mismatch_is_explicit_not_reencoded(self):
        raw=gif_bytes(9)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.gif';path.write_bytes(raw)
            result={'ok':True,'api_output_format':'gif','native_output':{'frame_count':4,'frame_duration_ms':160},'files':[str(path)]}
            generate.apply_media_metadata(result)
            self.assertTrue(result['animation_returned'])
            self.assertFalse(result['ok'])
            self.assertFalse(result['requested_output_satisfied'])
            self.assertEqual(result['error_kind'],'animation_controls_mismatch')
            self.assertFalse(result['animation_controls_satisfied'])
            self.assertEqual(len(result['animation_control_mismatches']),2)
            self.assertTrue(result['output_warning'])
            self.assertEqual(path.read_bytes(),raw)

    def test_exact_frames_and_durations_pass_without_duplicate_summary(self):
        raw=gif_bytes(4,160)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.gif';path.write_bytes(raw)
            result={'ok':True,'api_output_format':'gif','native_output':{'frame_count':4,'frame_duration_ms':160},'files':[str(path)],'display_summary':'实际尺寸：24×24'}
            generate.apply_media_metadata(result)
            generate.apply_media_metadata(result)
            self.assertTrue(result['ok'])
            self.assertTrue(result['requested_output_satisfied'])
            self.assertTrue(result['animation_controls_satisfied'])
            self.assertEqual(result['display_summary'].count('GIF 动图'),1)
            self.assertEqual(path.read_bytes(),raw)

    def test_frame_only_and_duration_only_requests_are_independent(self):
        cases=[({'frame_count':4},gif_bytes(4),True),({'frame_duration_ms':160},gif_bytes(9,160),True),({'frame_count':4},gif_bytes(9),False),({'frame_duration_ms':160},gif_bytes(4,[160,160,200,160]),False)]
        for controls,raw,expected in cases:
            with self.subTest(controls=controls,expected=expected),tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'image.gif';path.write_bytes(raw)
                result={'ok':True,'api_output_format':'gif','native_output':controls,'files':[str(path)]}
                generate.apply_media_metadata(result)
                self.assertEqual(result['ok'],expected)
                self.assertEqual(result['requested_output_satisfied'],expected)
                self.assertEqual(path.read_bytes(),raw)

    def test_cached_legacy_success_is_rechecked_against_original_bytes(self):
        raw=gif_bytes(9)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'original.gif';path.write_bytes(raw)
            cached={'ok':True,'api_output_format':'gif','native_output':{'frame_count':4,'frame_duration_ms':160},'files':[str(path)],'requested_output_satisfied':True,'media':[{'format':'gif','animated':True,'frame_count':4,'frame_durations_ms':[160]*4,'duration_ms':640}]}
            with mock.patch.object(generate,'_post_image_request') as post,mock.patch.object(generate,'_schedule_result_cleanup',return_value=True),contextlib.redirect_stdout(io.StringIO()):
                result=generate.emit_reused_success(cached,root,'task-legacy-gif',1)
            self.assertFalse(result['ok'])
            self.assertFalse(result['requested_output_satisfied'])
            self.assertEqual(result['media'][0]['frame_count'],9)
            post.assert_not_called()
            self.assertEqual(path.read_bytes(),raw)

    def test_mismatched_completed_response_recovers_and_remains_terminal(self):
        raw=gif_bytes(9)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record_path=root/'record.json';lock_path=root/'lock'
            record={'task_id':'task-gif-mismatch-recover','fingerprint':'b'*64,'request_context':{'n':1,'api_output_format':'gif','native_output':{'frame_count':4,'frame_duration_ms':160}},'delivery_response':{'data':[{'b64_json':base64.b64encode(raw).decode()}]}}
            record_path.write_text(json.dumps(record));lock_path.touch()
            with mock.patch.object(generate,'wait_for_task') as wait,mock.patch.object(generate,'_post_image_request') as post,mock.patch.object(generate,'_schedule_result_cleanup',return_value=True),contextlib.redirect_stdout(io.StringIO()):
                result=generate.recover_submitted_task(record,record_path,lock_path,'b'*64,root/'images','https://matrixapii.com/v1/images/generations','test',30)
            self.assertFalse(result['ok'])
            self.assertFalse(result['requested_output_satisfied'])
            stored=json.loads(record_path.read_text())
            self.assertEqual(stored['status'],'success')  # Paid operation finished; never reopen it for POST.
            self.assertFalse(stored['payload']['requested_output_satisfied'])
            self.assertEqual(Path(result['files'][0]).read_bytes(),raw)
            wait.assert_not_called();post.assert_not_called()

    def test_cli_default_gif_and_same_task_reuse_never_resubmit(self):
        raw=gif_bytes(9)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            argv=['generate.py','--prompt','动态钟摆 -f2.6','--size','896x896','--api-output-format','gif','--task-id','task-default-reuse','--out-dir',str(root/'images')]
            so,se=io.StringIO(),io.StringIO()
            with mock.patch.object(sys,'argv',argv),mock.patch.object(generate.Path,'home',return_value=root/'home'),mock.patch.object(generate,'discover_credentials',return_value=('https://matrixapii.com','test-key','gpt-image-2','test')),mock.patch.object(generate,'_post_image_request',return_value={'data':[{'b64_json':base64.b64encode(raw).decode()}]}) as post,mock.patch.object(generate,'_schedule_result_cleanup',return_value=True),contextlib.redirect_stdout(so),contextlib.redirect_stderr(se):
                first_code=generate.main()
                first=json.loads(so.getvalue());so.seek(0);so.truncate()
                second_code=generate.main()
                second=json.loads(so.getvalue())
            self.assertEqual(first_code,0,se.getvalue())
            self.assertEqual(second_code,0,se.getvalue())
            self.assertTrue(first['requested_output_satisfied'])
            self.assertTrue(second['requested_output_satisfied'])
            self.assertEqual(second['animation_control_policy'],'upstream_default')
            self.assertEqual(first['files'],second['files'])
            self.assertEqual(Path(second['files'][0]).read_bytes(),raw)
            self.assertEqual(post.call_count,1)

    def test_new_precise_frame_request_fails_before_paid_submit(self):
        for extra in (['--frame-count','4'],['--frame-duration-ms','160'],['--frame-count','9','--frame-duration-ms','200']):
            with self.subTest(extra=extra),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp)
                argv=['generate.py','--prompt','动态钟摆 -f2.6','--size','896x896','--api-output-format','gif',*extra,'--task-id','task-block-precise','--out-dir',str(root/'images')]
                so,se=io.StringIO(),io.StringIO()
                with mock.patch.object(sys,'argv',argv),mock.patch.object(generate.Path,'home',return_value=root/'home'),mock.patch.object(generate,'discover_credentials',return_value=('https://matrixapii.com','test-key','gpt-image-2','test')),mock.patch.object(generate,'_post_image_request') as post,contextlib.redirect_stdout(so),contextlib.redirect_stderr(se):
                    code=generate.main()
                self.assertNotEqual(code,0)
                self.assertIn('本次未发送、未计费',se.getvalue())
                post.assert_not_called()

    def test_existing_precise_cli_task_recovers_instead_of_new_request_guard(self):
        raw=gif_bytes(9)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'existing.gif';path.write_bytes(raw)
            cached={'ok':True,'task_id':'task-legacy-recover-cli','api_output_format':'gif','native_output':{'frame_count':4,'frame_duration_ms':160},'files':[str(path)],'requested_output_satisfied':True}
            argv=['generate.py','--prompt','动态钟摆 -f2.6','--size','896x896','--api-output-format','gif','--frame-count','4','--frame-duration-ms','160','--task-id','task-legacy-recover-cli','--out-dir',str(root/'images')]
            so,se=io.StringIO(),io.StringIO()
            with mock.patch.object(sys,'argv',argv),mock.patch.object(generate.Path,'home',return_value=root/'home'),mock.patch.object(generate,'discover_credentials',return_value=('https://matrixapii.com','test-key','gpt-image-2','test')),mock.patch.object(generate,'claim_idempotency',return_value=(root/'record.json',root/'lock','unused')) as claim,mock.patch.object(generate,'_post_image_request') as post,mock.patch.object(generate,'_schedule_result_cleanup',return_value=True),contextlib.redirect_stdout(so),contextlib.redirect_stderr(se):
                claim.return_value=(root/'record.json',root/'lock',cached)
                code=generate.main()
            result=json.loads(so.getvalue())
            self.assertNotEqual(code,0)
            self.assertEqual(result['error_kind'],'animation_controls_mismatch')
            self.assertEqual(result['animation_control_policy'],'explicit_legacy_request')
            self.assertTrue(result['idempotency_reused'])
            self.assertEqual(Path(result['files'][0]).read_bytes(),raw)
            post.assert_not_called()

    def test_completed_gif_response_recovers_without_upstream_request(self):
        raw=gif_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);record_path=root/'record.json';lock_path=root/'lock'
            record={'task_id':'task-gif-recover','fingerprint':'a'*64,'request_context':{'n':1,'api_output_format':'gif','native_output':{'frame_count':3}},'delivery_response':{'data':[{'b64_json':base64.b64encode(raw).decode()}]}}
            record_path.write_text(json.dumps(record));lock_path.touch()
            with mock.patch.object(generate,'wait_for_task') as wait,mock.patch.object(generate,'_post_image_request') as post,mock.patch.object(generate,'_schedule_result_cleanup',return_value=True),contextlib.redirect_stdout(io.StringIO()):
                result=generate.recover_submitted_task(record,record_path,lock_path,'a'*64,root/'images','https://matrixapii.com/v1/images/generations','test',30)
            self.assertEqual(post.call_count,0)
            self.assertEqual(wait.call_count,0)
            self.assertEqual(Path(result['files'][0]).read_bytes(),raw)

    def test_additional_gif_results_are_delivered_without_resubmit(self):
        raw=gif_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            argv=['generate.py','--prompt','蓝色钟摆 -f2.6','--size','896x896','--api-output-format','gif','--task-id','task-gif-two-items','--out-dir',str(Path(tmp)/'images')]
            so,se=io.StringIO(),io.StringIO()
            with mock.patch.object(sys,'argv',argv),mock.patch.object(generate.Path,'home',return_value=Path(tmp)/'home'),mock.patch.object(generate,'discover_credentials',return_value=('https://matrixapii.com','test-key','gpt-image-2','test')),mock.patch.object(generate,'_post_image_request',return_value={'data':[{'b64_json':base64.b64encode(raw).decode()}]*2}) as post,mock.patch.object(generate,'_schedule_result_cleanup',return_value=True),contextlib.redirect_stdout(so),contextlib.redirect_stderr(se):
                code=generate.main()
            self.assertEqual(code,0,se.getvalue())
            result=json.loads(so.getvalue())
            self.assertEqual(result['count'],2)
            self.assertEqual(result['requested_count'],1)
            self.assertTrue(result['additional_results_returned'])
            self.assertTrue(all(Path(path).is_file() for path in result['files']))
            self.assertEqual(post.call_count,1)

    def test_parallel_delivery_preserves_order_and_waits_for_every_file(self):
        lock=threading.Lock();active=0;maximum=0;calls=[]
        png=b'\x89PNG\r\n\x1a\n'+b'\x00\x00\x00\rIHDR'+(24).to_bytes(4,'big')+(24).to_bytes(4,'big')
        def download(url,endpoint,key,timeout,destination):
            nonlocal active,maximum
            with lock:active+=1;maximum=max(maximum,active);calls.append(url)
            time.sleep(0.04)
            data=png+url.encode();destination.write_bytes(data)
            with lock:active-=1
            return data,'image/png'
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(generate,'_download_image_to_path',side_effect=download):
            files=generate.save_images({'data':[{'url':f'https://example.com/{i}'} for i in range(4)]},'endpoint','test-key',Path(tmp),30,task_id='task-parallel',expected_count=4,download_workers=4)
            self.assertEqual(len(calls),4)
            self.assertGreater(maximum,1)
            self.assertLessEqual(maximum,4)
            for i,path in enumerate(files):self.assertTrue(Path(path).read_bytes().endswith(str(i).encode()))
            self.assertEqual(active,0)
            self.assertFalse((Path(tmp)/'.staging').exists())

    def test_parallel_download_failure_does_not_publish_partial_output(self):
        def download(url,endpoint,key,timeout,destination):
            destination.write_bytes(b'partial')
            raise generate.ImageGenError('one download failed')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(generate,'_download_image_to_path',side_effect=download):
            with self.assertRaises(generate.ImageGenError):
                generate.save_images({'data':[{'url':'https://example.com/image'}]*4},'endpoint','key',Path(tmp),30,task_id='task-failed',download_workers=4)
            self.assertFalse(list(Path(tmp).rglob('*.part')))
            self.assertFalse(list(Path(tmp).rglob('*.png')))

    def test_process_only_cannot_silently_ignore_native_gif_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'original.gif';path.write_bytes(gif_bytes())
            argv=['generate.py','--process-only','--image',str(path),'--api-output-format','gif','--output-format','png','--task-id','task-local-gif']
            so,se=io.StringIO(),io.StringIO()
            with mock.patch.object(sys,'argv',argv),mock.patch.object(generate,'_post_image_request') as post,mock.patch.object(generate,'process_many') as process,contextlib.redirect_stdout(so),contextlib.redirect_stderr(se):
                code=generate.main()
            self.assertNotEqual(code,0)
            post.assert_not_called();process.assert_not_called()

    def test_cli_native_gif_completes_once_with_timing(self):
        raw=gif_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            argv=['generate.py','--prompt','动态蓝色钟摆 -f2.6','--size','896x896','--api-output-format','gif','--task-id','task-native-gif','--out-dir',str(Path(tmp)/'images')]
            so,se=io.StringIO(),io.StringIO()
            with mock.patch.object(sys,'argv',argv),mock.patch.object(generate.Path,'home',return_value=Path(tmp)/'home'),mock.patch.object(generate,'discover_credentials',return_value=('https://matrixapii.com','test-key','gpt-image-2','test')),mock.patch.object(generate,'_post_image_request',return_value={'data':[{'b64_json':base64.b64encode(raw).decode()}]}) as post,mock.patch.object(generate,'_schedule_result_cleanup',return_value=True),contextlib.redirect_stdout(so),contextlib.redirect_stderr(se):
                code=generate.main()
            self.assertEqual(code,0,se.getvalue())
            result=json.loads(so.getvalue())
            self.assertTrue(result['ok'])
            self.assertTrue(result['requested_output_satisfied'])
            self.assertEqual(result['media'][0]['frame_count'],3)
            self.assertIn('delivery_timing',result)
            self.assertEqual(post.call_count,1)


if __name__=='__main__':unittest.main()
