# -*- coding: utf-8 -*-
{
    'name': 'ATMTA — Project Core',
    'version': '18.0.1.0.0',
    'category': 'Real Estate/Core',
    'summary': 'Authoritative definition of Project, Phase and plot boundary.',
    'description': """
ATMTA Project Core
==================

The single authoritative owner of the three models every other ATMTA
capability hangs off:

* ``realestate.project`` — identity, location, plot geometry, timeline and
  lifecycle of a development project;
* ``realestate.phase`` — an addressable stage of a project;
* ``realestate.project.boundary.point`` — one corner of the plot, from which
  the project centroid is derived.

It deliberately knows nothing about units, sales, construction, procurement or
money beyond the two headline planning figures. Anything that reads a property,
a contract, a budget line or a cost code belongs in a module *above* this one.

Extracted from ``real_estate_developer`` in ATMTA V2 Wave 2. The models, their
tables and their record identifiers are unchanged; only the module that
declares them moved.
""",
    'author': 'ATMTA',
    'license': 'LGPL-3',
    'depends': ['base', 'mail'],
    'data': [
        'data/project_sequence.xml',
        'security/project_core_rules.xml',
    ],
    'application': False,
    'installable': True,
    'auto_install': False,
}
