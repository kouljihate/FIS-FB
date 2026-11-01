"""test-9 - Report tab: the DPA sub-tab and the report it generates."""

import pytest

#: The report prints 22 chapters, listed on the DPA sub-tab.
CHAPTERS = 22


@pytest.mark.end_to_end
def test_dpa_subtab_shows_the_generate_button(ui, dataset):
    """Report → DPA holds the generate button next to the chapter list."""
    for project in dataset['projects']:
        pid = project['id']
        ui.goto(f'/report/{pid}/dpa')
        ui.assert_no_server_error(f'DPA sub-tab of project {pid}')
        ui.assert_no_danger(f'DPA sub-tab of project {pid}')

        subnav = ui.page.locator('.project-workflow-subnav')
        assert subnav.count() >= 1, 'the Report tab has no sub-tabs'
        assert 'DPA' in subnav.first.inner_text(), \
            f'the DPA sub-tab is missing from the Report tab: {subnav.first.inner_text()!r}'

        generate = ui.page.locator('a.dpa-generate')
        assert generate.count() == 1, \
            f'the DPA sub-tab of {project["name"]} has no generate button'
        target = generate.get_attribute('href') or ''
        assert target.endswith(f'/report/{pid}/dpa/print'), \
            f'the generate button does not point at the report: {target!r}'

        rows = ui.page.locator('table tbody tr')
        assert rows.count() == CHAPTERS, \
            f'the sub-tab should list {CHAPTERS} chapters, listed {rows.count()}'
        ui.assert_no_server_error('chapter list')

    project = dataset['projects'][0]
    ui.goto(f'/report/{project["id"]}/dpa')
    body = ui.body_text()
    assert project['name'] in body
    assert 'Contenu du rapport' in body, 'the sub-tab does not list the printed chapters'
    for chapter in ('Page de garde', 'Bilan hydrique', 'Devis / Offre',
                    'Catalogue des équipements'):
        assert chapter in body, f'{chapter} is missing from the chapter list'
    ui.shot('dpa-subtab')


@pytest.mark.end_to_end
def test_generate_button_opens_the_printable_report(ui, dataset):
    """The button actually builds the report, in its own printable page."""
    project = dataset['projects'][0]
    pid = project['id']
    ui.goto(f'/report/{pid}/dpa')

    with ui.page.expect_popup() as popup_info:
        ui.page.locator('a.dpa-generate').click()
    popup = popup_info.value
    popup.wait_for_load_state('domcontentloaded', timeout=30_000)
    assert popup.url.rstrip('/').endswith(f'/report/{pid}/dpa/print'), popup.url

    html = popup.content()
    assert 'RAPPORT TECHNIQUE' in html, 'the generated report has no title'
    assert 'sheet-foot' in html, 'the generated report has no legal footer'
    popup.close()


@pytest.mark.end_to_end
def test_report_for_every_project(ui, dataset):
    """Every project generates its own report."""
    for project in dataset['projects']:
        ui.goto(f'/report/{project["id"]}/dpa/print')
        ui.assert_no_server_error(f'report of project {project["id"]}')
        body = ui.body_text()
        assert 'RAPPORT TECHNIQUE' in body, f'report of {project["name"]} has no title'
        assert project['name'] in body
        assert 'Bilan Hydrique' in body
        assert 'Devis / Offre' in body


@pytest.mark.end_to_end
def test_report_has_header_and_footer_on_every_sheet(ui, dataset):
    """The company header and the legal footer belong to every printed page."""
    project = dataset['projects'][0]
    ui.goto(f'/report/{project["id"]}/dpa/print')
    ui.assert_no_server_error('DPA report')

    sheets = ui.page.locator('section.page')
    count = sheets.count()
    assert count == CHAPTERS, f'the report prints {CHAPTERS} sheets, got {count}'

    for index in range(count):
        sheet = sheets.nth(index)
        assert sheet.locator('header.sheet-head').count() == 1, \
            f'sheet {index + 1} has no company header'
        footer = sheet.locator('footer.sheet-foot')
        assert footer.count() == 1, f'sheet {index + 1} has no legal footer'
        footer_text = footer.inner_text()
        for label in ('ICE', 'I.F', 'Patente', 'RC', 'CNSS'):
            assert label in footer_text, f'{label} is missing from the footer of sheet {index + 1}'

    assert sheets.nth(0).locator('.cover-box').count() == 1

    # The report's default font is the bundled Comfortaa, not a system fallback.
    default_family = ui.page.evaluate(
        "() => getComputedStyle(document.body).fontFamily.split(',')[0]"
        ".replace(/[\"']/g, '').trim()")
    assert default_family == 'Comfortaa', f'the report uses {default_family!r}'
    faces = ui.page.evaluate(
        "async () => (await document.fonts.load('12px Comfortaa')).length")
    assert faces > 0, 'the Comfortaa faces declared by the report did not load'
    ui.shot('dpa-report')


@pytest.mark.end_to_end
def test_report_entries_are_printed(ui, dataset):
    """The Report tab lands on the DPA sub-tab, whose entries feed the report."""
    project = dataset['projects'][0]
    pid = project['id']

    # A typed URL for the old report lands on the DPA sub-tab, not on the print page.
    ui.goto(f'/engineering/{pid}/report/view')
    ui.assert_no_server_error('report tab')
    assert ui.page.locator('a.dpa-generate').count() == 1, \
        'the Report tab did not land on the DPA sub-tab'
    ui.assert_no_danger('report tab')

    # Header, footer and cover entries are saved on the report settings page.
    ui.goto(f'/report/{pid}/dpa/settings')
    ui.assert_no_server_error('report settings')
    ui.submit_form(f'/report/{pid}/dpa/settings', {
        'company_name': 'Chantier Irrigation SARL',
        'company_address': 'RDC 12 Lotissement Lalla Moulaty',
        'company_city': 'SEFROU',
        'company_phone': '0532066102',
        'legal_ice': '003011633000039',
        'legal_patente': '15301953',
        'legal_rc': '3695',
        'legal_cnss': '4087144',
        'meta_cr': 'TAZOUTA',
        'meta_province': 'SEFROU',
        'exp_soil': 'Limono-argileux',
        'exp_permeability': '8',
    })
    assert 'saved' in ui.body_text().lower()

    # The sub-tab reports the completed entries, and the report prints them.
    ui.goto(f'/report/{pid}/dpa')
    assert 'Prêt' in ui.body_text(), \
        'the DPA sub-tab does not report the completed entries'

    ui.goto(f'/report/{pid}/dpa/print')
    body = ui.body_text()
    for value in ('Chantier Irrigation SARL', '003011633000039', '15301953',
                  '3695', '4087144', 'TAZOUTA', 'Limono-argileux'):
        assert value in body, f'{value} is missing from the printed report'
    ui.shot('dpa-report-settings')
