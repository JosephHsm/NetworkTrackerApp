"""Connect latest exported research reports to the existing local report hub."""
import json,re
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit,unquote

from research_paths import ROOT, EXPORT as OUT
VIZ=ROOT/'viz_build'
def sync():
    mirror=True
    mapping={'핸드오버_정책_심화연구.html':'policy_research.html','research_progress.html':'research_progress.html','연구_4단계_진행점검.html':'research_stages.html'}
    if (OUT/'전체모델_정책비교.html').exists():mapping['전체모델_정책비교.html']='all_model_policy.html'
    for exported,local in mapping.items():
        page=(OUT/exported).read_text(encoding='utf-8')
        page=page.replace('href="file:///C:/Users/admin/NetworkTrackerApp/viz_build/index.html"','href="index.html"').replace('href="file:///C:/Users/admin/NetworkTrackerApp/viz_build/model_research.html"','href="model_research.html"')
        page=page.replace('href="보고서_모음.html"','href="index.html"')
        for a,b in mapping.items():page=page.replace(f'href="{a}"',f'href="{b}"')
        try:(VIZ/local).write_text(page,encoding='utf-8')
        except PermissionError:mirror=False
    for name in ('index.html','index.template.html'):
        path=VIZ/name;text=path.read_text(encoding='utf-8')
        if 'href="research_stages.html"' not in text:
            tile='\n      <a class="tile" href="research_stages.html" style="--c:var(--s3)"><b>AI 연구 4단계 진행 점검</b><span>질문·가설 → 비교 설계 → 원인 분석 → 새 데이터 검증 · 현재 위치와 다음 완료 조건</span></a>'
            updated,n=re.subn(r'(<a class="tile" href="research_progress\.html"[^>]*>.*?</a>)',lambda m:m[0]+tile,text,count=1,flags=re.S)
            if n!=1:raise RuntimeError('Existing readable-report hub tile not found')
            try:path.write_text(updated,encoding='utf-8')
            except PermissionError:mirror=False
    root=(VIZ/'index.html').read_text(encoding='utf-8')
    if (OUT/'전체모델_정책비교.html').exists():
        tile='\n      <a class="tile" href="all_model_policy.html" style="--c:var(--s1)"><b>전체 모델·정책 결합 비교</b><span>DQN+XGBoost/RNN/LSTM/GRU · Q/DQN/PPO/CQL · 공통 설정과 모델별 튜닝 분리</span></a>'
        for name in ('index.html','index.template.html'):
            path=VIZ/name;text=path.read_text(encoding='utf-8')
            if 'href="all_model_policy.html"' not in text:
                updated,n=re.subn(r'(<a class="tile" href="research_stages\.html"[^>]*>.*?</a>)',lambda m:m[0]+tile,text,count=1,flags=re.S)
                if n!=1:raise RuntimeError('Stage-report hub tile not found')
                try:path.write_text(updated,encoding='utf-8')
                except PermissionError:mirror=False
        root=(VIZ/'index.html').read_text(encoding='utf-8')
        if 'href="all_model_policy.html"' not in root:
            root=re.sub(r'(<a class="tile" href="research_stages\.html"[^>]*>.*?</a>)',lambda m:m[0]+tile,root,count=1,flags=re.S)
    latest={'policy_research.html':'핸드오버_정책_심화연구.html','research_progress.html':'research_progress.html','research_stages.html':'연구_4단계_진행점검.html','all_model_policy.html':'전체모델_정책비교.html'}
    def target(m):
        quote,url=m[1],m[2];parts=urlsplit(url)
        if parts.scheme or url.startswith('#'):return m[0]
        new=latest.get(parts.path)
        if not new:new=urlunsplit((*urlsplit((VIZ/unquote(parts.path)).resolve().as_uri())[:3],parts.query,parts.fragment))
        return f'href={quote}{new}{quote}'
    root=re.sub(r'href=([\"\'])(.*?)\1',target,root)
    (OUT/'보고서_모음.html').write_text(root,encoding='utf-8')
    return dict(original_hub_synced=mirror,latest_hub=str(OUT/'보고서_모음.html'))

if __name__=='__main__':print(json.dumps(sync(),ensure_ascii=False))
