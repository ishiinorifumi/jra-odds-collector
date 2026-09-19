// Google Apps Script(script.google.comの"collector-trigger"プロジェクトの写し)。
// 毎日6:30 JST前後(atHour(6).nearMinute(30))に、GitHubのworkflow_dispatchを呼ぶ。
// GH_TOKEN(Fine-grained PAT: 対象リポジトリのみ・Actions: Read and writeのみ)は
// スクリプトプロパティに保存する。コードには書かない。
// appsscript.jsonのtimeZoneは"Asia/Tokyo"にすること。
function dispatchCollector() {
  const token = PropertiesService.getScriptProperties().getProperty('GH_TOKEN');
  const url = 'https://api.github.com/repos/ishiinorifumi/jra-odds-collector/actions/workflows/collect.yml/dispatches';
  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ ref: 'master' }),
    muteHttpExceptions: true,
  });
  const code = res.getResponseCode();
  if (code !== 204) {
    throw new Error('dispatch failed: ' + code + ' ' + res.getContentText());
  }
  Logger.log('dispatch OK (204)');
}

// 1回だけ実行して日次トリガーを登録する
function installTrigger() {
  ScriptApp.newTrigger('dispatchCollector')
    .timeBased().atHour(6).nearMinute(30).everyDays(1).create();
}
