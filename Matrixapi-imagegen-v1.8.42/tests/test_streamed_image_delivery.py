import http.server
import importlib.util
from pathlib import Path
import socketserver
import sys
import tempfile
import threading
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "skills" / "Matrixapi-imagegen" / "scripts" / "generate.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("matrixapi_generate", SCRIPT_PATH)
GENERATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GENERATE)


class _ImageHandler(http.server.BaseHTTPRequestHandler):
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * (2 * 1024 * 1024)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()
        for start in range(0, len(self.payload), 4096):
            self.wfile.write(self.payload[start : start + 4096])

    def log_message(self, *_args):
        return


class StreamedImageDeliveryTest(unittest.TestCase):
    def test_result_url_streams_to_part_file_then_publishes(self):
        server = socketserver.TCPServer(("127.0.0.1", 0), _ImageHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                files = GENERATE.save_images(
                    {"data": [{"url": f"http://127.0.0.1:{server.server_address[1]}/image"}]},
                    "http://127.0.0.1:3000/v1/images/generations",
                    "",
                    Path(temp_dir),
                    30,
                )
                self.assertEqual(1, len(files))
                output = Path(files[0])
                self.assertEqual(_ImageHandler.payload, output.read_bytes())
                self.assertFalse(list(Path(temp_dir).glob("*.part")))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
