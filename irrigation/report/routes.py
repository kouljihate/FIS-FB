"""Report pages: the DPA sub-tab, the printable report it generates and its entries."""

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from irrigation.access import editor_required, require_project
from irrigation.report.dpa import build_context, save_settings

report_bp = Blueprint('report', __name__)


def _project_or_redirect(project_id):
    project = require_project(project_id)
    if not project:
        return None
    return project


def _sheets(context):
    """The chapters the report prints, each with the entry that feeds it.

    ``ready`` only says the source entry is available; a chapter whose entry is
    missing still prints, with a labelled placeholder.
    """
    project_id = context['project']['id']
    settings_url = url_for('report.dpa_settings', project_id=project_id)
    water_url = url_for('water.well', project_id=project_id)
    geometry_url = url_for('geometry.sectors_view', project_id=project_id)
    pipes_url = url_for('hydrology.main_pipe', project_id=project_id)
    trees_url = url_for('trees.rows_view', project_id=project_id)
    estimation_url = url_for('estimation.quote_view', project_id=project_id)

    company = context['company'] or {}
    legal = context['legal'] or {}
    documents = {slot: (context['documents'].get(slot) or {}).get('path')
                 for slot, _label in context['doc_slots']}

    def has(slot):
        return bool(documents.get(slot))

    rows = (
        ('1', 'Page de garde', 'En-tête, logo, client et date',
         bool(company.get('name') and legal.get('ice')), settings_url),
        ('2', "Carte Nationale d'Identité (recto-verso)", 'Scans CIN — recto et verso',
         has('cin_front') and has('cin_back'), settings_url),
        ('3', 'Attestation CNSS du client', 'Scan de l’attestation CNSS',
         has('cnss'), settings_url),
        ('4', 'Attestation de creusement du puits', 'Scan de l’attestation',
         has('well'), settings_url),
        ('5', 'Engagement fournisseur', 'Scan de l’engagement', has('supplier'), settings_url),
        ('6', 'Certificat de qualification et de classification', 'Scan du certificat',
         has('qualification'), settings_url),
        ('7', "Identification de l'exploitation",
         'Secteurs et zones + superficie, sol, topographie', bool(context['sectors']),
         geometry_url),
        ('8', 'Besoins en eau et calculs hydrauliques', 'Arbres, ETo et pluie utile',
         bool(context['trees']), trees_url),
        ('9', "Postes d'irrigation", 'Zones, arbres et rampes', bool(context['posts']),
         geometry_url),
        ('10', 'Rampes', 'Lignes de plantation (DripLine)', bool(context['rampes']), pipes_url),
        ('11', "Porte-rampes, antennes secondaires, têtes d'unités et conduites principales",
         'Réseau MP / SP', bool(context['main_rows'] or context['zone_pipes']), pipes_url),
        ('12', 'Goutteur', 'Arbres et bulletin d’essai', bool(context['trees']), trees_url),
        ('13', 'Les tronçons', 'Conduites, débits et vitesses', bool(context['troncons']),
         pipes_url),
        ('14', 'Les pressions', 'Pertes de charge et pression de service',
         bool(context['pressions']), pipes_url),
        ('15', 'Groupe motopompe pour forage', 'Source d’eau — forage', bool(context['well']),
         water_url),
        ('16', 'Groupe motopompe pour bassin', 'Source d’eau — bassin',
         bool(context['basin_source']), water_url),
        ('17', 'Bassin de stockage — dimensions et volume', 'Bassin',
         bool(context['basin_source']), water_url),
        ('18', 'Bilan hydrique', 'Besoins, pluie et débit de la source',
         bool(context['trees'] and context['source_flow_known']), trees_url),
        ('19', 'Devis / Offre', 'Estimation', bool((context['quote'] or {}).get('has_lines')),
         estimation_url),
        ('20', 'Plans — sectorisation, réseau, bassin, station de tête', 'Scans des plans',
         all(has(slot) for slot in ('plan_sectors', 'plan_network', 'plan_basin',
                                    'plan_head')), settings_url),
        ('21', "Bulletin d'essai", 'Scan + marque, modèle, pression et débit',
         bool(has('bulletin') or (context['settings']['essai'] or {}).get('brand')),
         settings_url),
        ('22', 'Catalogue des équipements', 'Scan du catalogue', has('catalogue'),
         settings_url),
    )
    return [{'page': page, 'title': title, 'entry': entry, 'ready': ready, 'href': href}
            for page, title, entry, ready, href in rows]


@report_bp.route('/<int:project_id>/dpa')
@login_required
def dpa_view(project_id):
    """Report → DPA sub-tab: what the report prints and the button that builds it."""
    project = _project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    context = build_context(project)
    sheets = _sheets(context)
    return render_template('report/dpa_tab.html', sheets=sheets,
                           ready_count=sum(1 for sheet in sheets if sheet['ready']),
                           **context)


@report_bp.route('/<int:project_id>/dpa/print')
@login_required
def dpa_print(project_id):
    """The printable DPA report, built from the project's own entries."""
    project = _project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))
    return render_template('report/dpa.html', **build_context(project))


@report_bp.route('/<int:project_id>/dpa/settings', methods=['GET', 'POST'])
@login_required
@editor_required
def dpa_settings(project_id):
    """Header, footer, cover and document scans of the DPA report."""
    project = _project_or_redirect(project_id)
    if not project:
        return redirect(url_for('project.index'))

    if request.method == 'POST':
        save_settings(project_id, project, request.form, request.files)
        flash('Report settings saved / تم حفظ إعدادات التقرير', 'success')
        return redirect(url_for('report.dpa_settings', project_id=project_id))

    return render_template('report/dpa_settings.html', **build_context(project))
