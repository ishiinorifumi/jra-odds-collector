"""オッズ表示ページのHTMLから、実際のオッズ数値を抽出する。"""
import re
from bs4 import BeautifulSoup


def parse_tanpuku(html):
    """単勝・複勝オッズ（馬番順）ページを解析する。
    戻り値: [{"umaban": "1", "horse_name": "...", "tan_odds": "85.1",
              "fuku_min": "7.1", "fuku_max": "12.0", "weight": "490", ...}, ...]
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("table.tanpuku")
    if table is None:
        return None

    rows = []
    for tr in table.select("tbody tr"):
        umaban = tr.select_one("td.num")
        horse = tr.select_one("td.horse")
        tan = tr.select_one("td.odds_tan")
        fuku = tr.select_one("td.odds_fuku")
        age = tr.select_one("td.age")
        weight = tr.select_one("td.h_weight")
        kinryo = tr.select_one("td.weight")
        jockey = tr.select_one("td.jockey")
        trainer = tr.select_one("td.trainer")

        if umaban is None:
            continue

        fuku_min = fuku_max = None
        if fuku is not None:
            mn = fuku.select_one("span.min")
            mx = fuku.select_one("span.max")
            fuku_min = mn.get_text(strip=True) if mn else None
            fuku_max = mx.get_text(strip=True) if mx else None

        weight_val = weight_diff = None
        if weight is not None:
            full_text = weight.get_text(" ", strip=True)
            parts = full_text.split()
            weight_val = parts[0] if parts else None
            weight_diff = parts[1].strip("()") if len(parts) > 1 else None

        rows.append({
            "umaban": umaban.get_text(strip=True),
            "horse_name": horse.get_text(strip=True) if horse else None,
            "tan_odds": tan.get_text(strip=True) if tan else None,
            "fuku_min": fuku_min,
            "fuku_max": fuku_max,
            "age": age.get_text(strip=True) if age else None,
            "weight": weight_val,
            "weight_diff": weight_diff,
            "kinryo": kinryo.get_text(strip=True) if kinryo else None,
            "jockey": jockey.get_text(strip=True) if jockey else None,
            "trainer": trainer.get_text(strip=True) if trainer else None,
        })
    return rows


def parse_combination_table(html, table_class_hint=None):
    """馬連・ワイド・馬単・3連複・3連単など、組番×オッズのテーブルを汎用的に解析する。
    構造がベットタイプごとに異なるため、まずは全<table>から
    (行の全セルテキストのリスト)を素直に抜き出す形にとどめる。
    生HTMLは別途保存するため、ここでの解析精度は将来的に個別改善する前提。
    """
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    result = []
    for table in tables:
        cls = table.get("class")
        rows = []
        for tr in table.select("tr"):
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
        result.append({"class": cls, "rows": rows})
    return result


_STATUS_TS_RE = re.compile(r"(\d{1,2})時(\d{1,2})分現在オッズ")


def get_odds_status_label(html):
    """このページのオッズがどの時点のものかを示すラベルを抽出する。

    2026-09-19〜22の実データで確認した結果、発走前ページの実表記は
    「X時Y分現在オッズ」という更新時刻つきの表示であり、固定文言の
    「最終オッズ」「中間オッズ」等ではなかった(これらの語は発売締切後の
    挙動を説明する脚注テキストにのみ含まれており、旧実装はそこに常に
    誤マッチして発走90分前も含め全時点で「最終オッズ」を返していた)。

    戻り値は "HH:MM現在" 形式(サイト自身が「この時点のオッズ」として
    示す時刻。当方の取得時刻とのズレ=サイト側の更新遅延の目安になる)、
    または決着後ページで実際に確認した「最終オッズ」。発売締切直後〜決着前の
    状態(推測: 「発売締切」等)は未確認。どれにも一致しなければ"unknown"。"""
    soup = BeautifulSoup(html, "html.parser")
    # ページ末尾の注記(div.caution、「発売締切直後に表示される最終オッズは...」)に
    # "確定オッズ"以外の探索語がすべて含まれており、素のテキスト検索だと常に
    # ここへ誤マッチする。注記を木から除去してから、本来の更新時刻表示
    # (div.refresh_line内)に絞って検索する。
    caution = soup.select_one("div.caution")
    if caution is not None:
        caution.decompose()
    refresh = soup.select_one("div.refresh_line")
    scope_text = refresh.get_text() if refresh is not None else soup.get_text()
    for label in ["確定オッズ", "最終オッズ", "発売締切"]:
        if label in scope_text:
            return label
    m = _STATUS_TS_RE.search(scope_text)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}現在"
    return "unknown"
