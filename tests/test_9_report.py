"""test-9 - Report tab: the printable report placeholder and the technical report."""

import pytest


@pytest.mark.end_to_end
def test_report_tab_for_every_project(ui, dataset):
    for project in dataset['projects']:
        ui.goto(f'/engineering/{project["id"]}/report/view')
        ui.assert_no_server_error(f'report of project {project["id"]}')
        body = ui.body_text()
        assert 'Report' in body
        assert project['name'] in body


@pytest.mark.end_to_end
def test_report_placeholder_and_technical_report(ui, dataset):
    project = dataset['projects'][0]
    pid = project['id']

    ui.goto(f'/engineering/{pid}/report/view')
    body = ui.body_text()
    assert 'Coming Soon' in body
    ui.shot('report-placeholder')

    # The Technical (DPA) sub-tab is reachable and renders its own page.
    ui.goto(f'/engineering/{pid}/technical-report')
    ui.assert_no_server_error('technical report')
    body = ui.body_text()
    assert 'Technical Report' in body
    subnav = ui.page.locator('nav.project-workflow-subnav').first.inner_text()
    assert 'Technical (DPA)' in subnav
    ui.shot('technical-report')
