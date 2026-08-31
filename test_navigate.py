import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def do_action(page, path, cname):
    """サイト自身のdoAction()をブラウザ内で直接実行し、実フォーム送信でページ遷移する。"""
    with page.expect_navigation(wait_until="networkidle"):
        page.evaluate(
            "([path, cname]) => doAction(path, cname)",
            [path, cname],
        )


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=UA)
        page = context.new_page()

        print("=== トップページへ ===")
        page.goto("https://www.jra.go.jp/", wait_until="networkidle")

        print("=== doAction()でオッズ開催選択へ ===")
        do_action(page, "/JRADB/accessO.html", "pw15oli00/6D")
        print("タイトル:", page.title())
        has_niigata = "3回新潟4日" in page.content()
        print("'3回新潟4日'を含むか:", has_niigata)

        if not has_niigata:
            print(page.content()[:2000])
            browser.close()
            return

        # レース選択ページ側のonclick属性からcnameを抽出
        onclick = page.eval_on_selector(
            "a:has-text('3回新潟4日')",
            "el => el.getAttribute('onclick')",
        )
        print("3回新潟4日のonclick:", onclick)

        import re
        m = re.search(r"doAction\('([^']+)',\s*'([^']+)'\)", onclick)
        path2, cname2 = m.group(1), m.group(2)

        print("\n=== レース選択ページへ ===")
        do_action(page, path2, cname2)
        print("タイトル:", page.title())
        has_kinen = "新潟記念" in page.content()
        print("'新潟記念'を含むか:", has_kinen)

        # 新潟記念の単勝複勝ボタンのonclickを取得
        # 新潟記念を含む行(tr)内の最初のリンクを探す
        onclick3 = page.eval_on_selector(
            "xpath=//*[contains(text(),'新潟記念')]/ancestor::tr[1]//a[contains(@onclick,'doAction')]",
            "el => el.getAttribute('onclick')",
        )
        print("新潟記念 単勝複勝のonclick:", onclick3)
        m3 = re.search(r"doAction\('([^']+)',\s*'([^']+)'\)", onclick3)
        path3, cname3 = m3.group(1), m3.group(2)

        print("\n=== オッズ表示ページへ ===")
        do_action(page, path3, cname3)
        print("タイトル:", page.title())
        text = page.inner_text("body")
        idx = text.find("新潟記念")
        print(text[idx:idx + 800] if idx >= 0 else text[:800])

        browser.close()


if __name__ == "__main__":
    main()
