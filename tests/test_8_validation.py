"""test-8 - Validation tab: run the checks and read the result."""

import pytest


@pytest.mark.end_to_end
def test_validation_page_for_every_project(ui, dataset):
    for project in dataset['projects']:
        ui.goto(f'/engineering/{project["id"]}')
        ui.assert_no_server_error(f'validation of project {project["id"]}')
        assert ui.page.locator("form[action$='/run']").count() == 1
        assert 'Validation' in ui.body_text()


@pytest.mark.end_to_end
def test_run_the_validation(ui, dataset):
    pid = dataset['projects'][0]['id']
    ui.goto(f'/engineering/{pid}')

    ui.submit_form('/run')
    # The suite checks the whole project: passes, warnings or failures.
    flash = ui.expect_flash(r'Validation', timeout=180_000)
    assert 'Invalid parameter' not in flash

    table = ui.page.locator('#validationResultsTable')
    assert table.count() == 1, 'the validation result table is missing'
    rows = table.locator('tbody tr[data-severity]').count()
    assert rows >= 3, f'only {rows} check rows were rendered'
    ui.shot('validation-result')

    # The result is kept: reloading the tab shows the same run.
    ui.goto(f'/engineering/{pid}')
    assert ui.page.locator('#validationResultsTable tbody tr[data-severity]').count() == rows
