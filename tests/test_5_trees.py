"""test-5 - Trees tab: rows, validation, tree generation."""

import re

import pytest


@pytest.mark.end_to_end
def test_trees_subtabs_render_for_every_project(ui, dataset):
    for project in dataset['projects']:
        for slug, heading in (('rows', 'Rows'), ('trees', 'Trees')):
            ui.goto(f'/{project["id"]}/{slug}')
            ui.assert_no_server_error(f'{slug} of project {project["id"]}')
            assert heading in ui.body_text()
            subnav = ui.page.locator('nav.project-workflow-subnav').first.inner_text()
            assert 'Rows' in subnav and 'Trees' in subnav


@pytest.mark.end_to_end
def test_generate_and_validate_rows(ui, dataset):
    pid = dataset['projects'][0]['id']
    ui.goto(f'/{pid}/rows')

    assert ui.page.locator('[data-bs-target="#generateRowsModal"]').count() == 1
    ui.submit_modal('generateRowsModal', {'spacing': '5', 'replace': True})
    flash = ui.expect_flash(r'Generated \d+ rows', kind='success', timeout=120_000)
    ui.assert_no_danger('row generation')
    rows = int(next(iter(re.findall(r'\d+', flash))))
    assert rows >= 1

    ui.submit_form('/rows/validate')
    ui.expect_flash(r'Rows validated', kind='success')
    ui.shot('rows-validated')

    # With the rows validated the DripLine stage opens up.
    ui.goto(f'/{pid}/main_pipe')
    dripline = ui.page.locator('nav.project-workflow-subnav').first.locator('text=DripLine').first
    assert dripline.evaluate("el => el.closest('a') !== null"), \
        'DripLine stays disabled after the rows were validated'


@pytest.mark.end_to_end
def test_generate_and_validate_trees(ui, dataset):
    pid = dataset['projects'][0]['id']
    ui.goto(f'/{pid}/trees')

    ui.submit_modal('treeSetupModal', {'spacing_all': '4', 'drips_all': '2'})
    flash = ui.expect_flash(r'Generated \d+ trees', kind='success', timeout=180_000)
    ui.assert_no_danger('tree generation')
    trees = int(next(iter(re.findall(r'\d+', flash))))
    assert trees >= 1
    ui.shot('trees-generated')

    # The Validate circle button posts the form it builds in JavaScript.
    ui.click('#treeValidateBtn')
    ui.expect_flash(r'Trees validated', kind='success')
