const GITHUB_OWNER = "Therrayyyy";
const GITHUB_REPOSITORY = "TFT_Series_NOGI";
const GITHUB_WORKFLOW = "public-tournament-sync.yml";
const GITHUB_BRANCH = "main";

function installRegistrationTrigger() {
  const token = PropertiesService.getScriptProperties()
    .getProperty("GITHUB_DISPATCH_TOKEN");
  if (!token) {
    throw new Error(
      "Add GITHUB_DISPATCH_TOKEN in Apps Script project properties first."
    );
  }

  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  if (!spreadsheet) {
    throw new Error("Open this script from the registrations response spreadsheet.");
  }

  ScriptApp.getProjectTriggers()
    .filter((trigger) => trigger.getHandlerFunction() === "onRegistrationSubmitted")
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));

  ScriptApp.newTrigger("onRegistrationSubmitted")
    .forSpreadsheet(spreadsheet)
    .onFormSubmit()
    .create();
}

function onRegistrationSubmitted(event) {
  if (!event || !event.range) {
    throw new Error("This function must be called by the spreadsheet form trigger.");
  }

  const token = PropertiesService.getScriptProperties()
    .getProperty("GITHUB_DISPATCH_TOKEN");
  if (!token) {
    throw new Error("GITHUB_DISPATCH_TOKEN is not configured in script properties.");
  }

  const response = UrlFetchApp.fetch(
    `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPOSITORY}` +
      `/actions/workflows/${GITHUB_WORKFLOW}/dispatches`,
    {
      method: "post",
      contentType: "application/json",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
      },
      payload: JSON.stringify({
        ref: GITHUB_BRANCH,
        inputs: { reason: "google_form_submission" },
      }),
      muteHttpExceptions: true,
    }
  );

  if (response.getResponseCode() !== 204) {
    throw new Error(
      `GitHub workflow dispatch failed (HTTP ${response.getResponseCode()}).`
    );
  }
}
