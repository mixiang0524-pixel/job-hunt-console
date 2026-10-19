"""将旧页面的现有信息改为新版视觉层级；不读取、重算或替换岗位数据。"""
import argparse
import re
from pathlib import Path


def restyle(document):
    css = (Path(__file__).resolve().parent.parent / 'templates' / 'legacy-console.css').read_text(encoding='utf-8')
    document = re.sub(r'<style>.*?</style>', lambda _: '<style>' + css + '</style>', document, count=1, flags=re.S)
    document = re.sub(r'<link[^>]*https://fonts\.(?:googleapis|gstatic)\.com[^>]*>\s*', '', document)
    if '<!-- legacy-console-restyled -->' in document:
        return document

    document = document.replace('<body>', '<body><!-- legacy-console-restyled -->', 1)
    # 旧的介绍含错误的自动改状态规则。事实数字和评分说明仍保留。
    document = document.replace('展开看过 JD 会自动记为「不感兴趣」，再点一下就是「已投递」。',
                                '展开职位描述只记录「看过」，投递状态由你点击修改。')
    document = re.sub(r'(<p class="sub">.*?</p>)', r'<details class="intro"><summary>数据与使用说明</summary>\1</details>', document, count=1, flags=re.S)
    document = re.sub(
        r'(<section class="panel[^"]*" data-city="[^"]+"><div class="wrap">)(<div class="stats">.*?)(<div class="sec-head"><h2>.*?</h2>)(<span class="sec-note">.*?</span>)(</div>)',
        r'\1<details class="city-overview"><summary>岗位概况与筛选说明</summary>\2\4</details>\3\5', document, flags=re.S)
    document = re.sub(
        r'(<button class="btn" id="exp".*?<input id="impf"[^>]*>)',
        r'<details class="data-tools"><summary>数据管理</summary>\1</details>', document, count=1, flags=re.S)
    document = re.sub(r'<div class="jd">(.*?)</div>',
                      r'<details class="jd-fold"><summary>职位描述全文</summary><div class="jd">\1</div></details>', document, flags=re.S)
    document = re.sub(r'<footer><div class="wrap">(.*?)</div></footer>',
                      r'<footer><div class="wrap"><details><summary>记录保存、评分与标记说明</summary><div>\1</div></details></div></footer>', document, count=1, flags=re.S)
    document = document.replace('<span class="title">', '<span class="title" role="button" tabindex="0" aria-expanded="false">')
    document = document.replace('<span style="color:var(--accent)">■</span> 扩展词',
                                '<span style="color:var(--ink-3)">■</span> 扩展词')
    document = document.replace('const LABEL={0:"待定",1:"不感兴趣",2:"已投递"};',
                                'const LABEL={0:"待定",1:"不感兴趣",2:"已投递",3:"停止招聘"};')
    # 同一存储键可能含新版的看过或屏蔽字段，保存旧版状态时不能把它们抹掉。
    document = document.replace('function save(){localStorage.setItem(KEY,JSON.stringify({v:2,at:Date.now(),items:store}));}',
        'function save(){let raw={};try{raw=JSON.parse(localStorage.getItem(KEY)||"{}");}catch{}'
        'localStorage.setItem(KEY,JSON.stringify({...raw,v:2,at:Date.now(),items:store}));}')
    document = document.replace('if(s===0)delete store[id]; else store[id]={s:s,t:Date.now()};',
                                'store[id]={...store[id],s:s,t:Date.now()};')
    document = document.replace('setSt(tr.dataset.id,(stOf(tr.dataset.id)+1)%3)',
                                'setSt(tr.dataset.id,stOf(tr.dataset.id)===3?0:(stOf(tr.dataset.id)+1)%3)')
    document = document.replace('else c2++;', 'else if(s===2)c2++;')
    document = document.replace('const btn=tr.querySelector(".stbtn");',
        'tr.classList.toggle("seen",!!store[id]?.v);\n      const btn=tr.querySelector(".stbtn");')
    document = document.replace('det.classList.remove("show");}',
                                'det.classList.remove("show");tr.querySelector(".title").setAttribute("aria-expanded","false");}')
    document = document.replace('tr.classList.toggle("open",open); det.classList.toggle("show",open);',
        'tr.classList.toggle("open",open); det.classList.toggle("show",open);\n    t.setAttribute("aria-expanded",String(open));')
    document = document.replace('// 浏览过即视为「不感兴趣」，再点状态钮一次就是「已投递」\n    if(open&&stOf(tr.dataset.id)===0){setSt(tr.dataset.id,1);refresh();}',
        'if(open&&!store[tr.dataset.id]?.v){store[tr.dataset.id]={...store[tr.dataset.id],v:Date.now()};save();refresh();}')
    document = document.replace('setTimeout(()=>URL.revokeObjectURL(a.href),3000);', 'URL.revokeObjectURL(a.href);')
    document = document.replace('sortAll(); refresh();\n</script>',
        'document.addEventListener("keydown",e=>{if(e.target.matches(".title")&&(e.key==="Enter"||e.key===" ")){e.preventDefault();e.target.click();}});\n'
        'sortAll(); refresh();\n</script>')
    return document


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    Path(args.output).write_text(restyle(Path(args.input).read_text(encoding='utf-8')), encoding='utf-8')
