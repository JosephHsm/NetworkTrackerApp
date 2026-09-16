"""
리포트·지도를 로컬 서버로 연다.

OSM 상세 지도는 HTML을 더블클릭해 열면(file://, Referer 없음) "Access blocked" 이미지로 막힌다.
http://127.0.0.1 로 열면 브라우저가 Referer를 보내서 19단계까지 정상으로 나온다.

사용법:
    python analysis/serve_reports.py                      # viz_build/report_all.html
    python analysis/serve_reports.py viz_build/report_car.html
    python analysis/serve_reports.py analysis_output/network_log_20260915_165206_subway_mapmatched_map.html
창을 닫거나 Ctrl+C로 서버를 끈다.
"""
import sys, os, functools, webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

PORT = 8765
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 저장소 루트


class NoCacheHandler(SimpleHTTPRequestHandler):
    # 리포트를 다시 빌드해도 브라우저가 옛 파일을 캐시에서 꺼내지 않게 한다
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass


def main():
    page = (sys.argv[1] if len(sys.argv) > 1 else "viz_build/report_all.html").replace("\\", "/")
    handler = functools.partial(NoCacheHandler, directory=ROOT)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    except OSError:
        server = None   # 이미 다른 창에서 떠 있으면 그 서버를 쓴다
    url = f"http://127.0.0.1:{PORT}/{page}"
    webbrowser.open(url)
    if server is None:
        print(f"서버가 이미 실행 중입니다 → {url}")
        return
    print(f"리포트 서버 실행 중 → {url}")
    print("다른 리포트: viz_build/report_car.html, viz_build/report_subway.html, analysis_output/*_map.html")
    print("이 창을 닫으면 서버가 꺼집니다.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
