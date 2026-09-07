"""JRA公式サイトのオッズページを、Playwrightの実ブラウザ経由(doAction()の直接呼び出し)で
辿るためのヘルパー関数群。単純なHTTPクライアント(requests/fetch)は弾かれることが
確認済みのため、必ずPlaywrightの実ページコンテキスト上でdoAction()を呼び出すこと。

cnameの構造は解析済み:
  開催: pw15orl10 + 場コード(2) + 年(4) + 回次(2) + 日次(2) + 日付8桁 + "/" + checksum(2)
  レース+単勝複勝: pw151ou10 + 場コード(2) + 年(4) + 回次(2) + 日次(2) + レース番号(2) + 日付8桁 + "Z/" + checksum(2)
  枠連=pw153ou.. 馬連=pw154ou.. ワイド=pw155ou.. 馬単=pw156ou.. 3連複=pw157ou.. 3連単=pw158ou..
checksumはサーバー生成で予測不可能なため、必ず前段のページから実際に抽出すること。
"""
import re
from bs4 import BeautifulSoup

DOACTION_RE = re.compile(r"doAction\('([^']+)'\s*,\s*'([^']+)'\)")

COURSE_NAME_TO_CODE = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04", "東京": "05",
    "中山": "06", "中京": "07", "京都": "08", "阪神": "09", "小倉": "10",
}

ODDS_ENTRY_PATH = "/JRADB/accessO.html"
ODDS_ENTRY_CNAME = "pw15oli00/6D"

BETTYPE_CLASS_TO_LABEL = {
    "tanpuku": "単勝複勝",
    "wakuren": "枠連",
    "umaren": "馬連",
    "wide": "ワイド",
    "umatan": "馬単",
    "trio": "3連複",
    "tierce": "3連単",
}


def do_action(page, path, cname):
    with page.expect_navigation(wait_until="networkidle"):
        page.evaluate("([p, c]) => doAction(p, c)", [path, cname])


def goto_odds_top(page):
    page.goto("https://www.jra.go.jp/", wait_until="networkidle")
    do_action(page, ODDS_ENTRY_PATH, ODDS_ENTRY_CNAME)


def _extract_action(onclick_or_href):
    if not onclick_or_href:
        return None
    m = DOACTION_RE.search(onclick_or_href)
    return (m.group(1), m.group(2)) if m else None


def list_meetings(html):
    """オッズ開催選択ページのHTMLから、日付ごとの開催一覧を抽出する。
    戻り値: [{"date_label": "8月30日（日曜）", "meeting_label": "3回新潟4日",
              "course_code": "04", "kaiji": "03", "nichiji": "04",
              "date": "20260830", "path": ..., "cname": ...}, ...]
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for a in soup.find_all("a"):
        onclick = a.get("onclick")
        action = _extract_action(onclick)
        if not action:
            continue
        path, cname = action
        # 2026-09-05に実運用で確認: プレフィックスは"pw15orl10"固定ではなく
        # 末尾2桁が可変("pw15orl00"等、時期により変わる可能性がある)。
        # 該当部分は桁数のみ固定して受け入れる。
        m = re.match(r"pw15orl\d{2}(\d{2})(\d{4})(\d{2})(\d{2})(\d{8})/", cname)
        if not m:
            continue
        course_code, year, kaiji, nichiji, date = m.groups()
        results.append({
            "meeting_label": a.get_text(strip=True),
            "course_code": course_code,
            "year": year,
            "kaiji": kaiji,
            "nichiji": nichiji,
            "date": date,
            "path": path,
            "cname": cname,
        })
    return results


def list_races(html):
    """オッズレース選択ページのHTMLから、各レースの発走時刻と馬券種別ごとの
    doActionを抽出する。
    戻り値: [{"race_no": "08", "post_time": "発走済" or "15時45分",
              "race_name": "...", "bettypes": {"単勝複勝": (path,cname), ...}}, ...]
    """
    soup = BeautifulSoup(html, "html.parser")
    races = []
    for tr in soup.select("table tr"):
        odds_td = tr.select_one("td.odds")
        time_td = tr.select_one("td.time")
        if odds_td is None or time_td is None:
            continue

        race_no = None
        race_num_th = tr.select_one("th.race_num img")
        if race_num_th and race_num_th.get("alt"):
            m = re.search(r"(\d+)", race_num_th["alt"])
            if m:
                race_no = m.group(1).zfill(2)

        name_td = tr.select_one("td.race_name")
        race_name = name_td.get_text(" ", strip=True) if name_td else ""

        bettypes = {}
        for cls, label in BETTYPE_CLASS_TO_LABEL.items():
            div = odds_td.select_one(f"div.{cls} a")
            if div is None:
                continue
            action = _extract_action(div.get("onclick") or div.get("href"))
            if action:
                bettypes[label] = action

        races.append({
            "race_no": race_no,
            "post_time": time_td.get_text(strip=True),
            "race_name": race_name,
            "bettypes": bettypes,
        })
    return races


def get_html(page, path, cname):
    do_action(page, path, cname)
    return page.content()


def refresh_race_action(page, meeting, race_no, bet_type):
    """cnameのchecksumが失効している可能性があるcapture失敗時に、
    開催選択→レース選択を辿り直して該当レース・馬券種の最新(path, cname)を再取得する。
    見つからなければNoneを返す。"""
    goto_odds_top(page)
    html_top = page.content()
    meetings = list_meetings(html_top)
    match = next(
        (m for m in meetings
         if m["course_code"] == meeting["course_code"]
         and m["kaiji"] == meeting["kaiji"]
         and m["nichiji"] == meeting["nichiji"]
         and m["date"] == meeting["date"]),
        None,
    )
    if match is None:
        return None

    html_races = get_html(page, match["path"], match["cname"])
    races = list_races(html_races)
    race_match = next((r for r in races if r["race_no"] == race_no), None)
    if race_match is None:
        return None

    return race_match["bettypes"].get(bet_type)
