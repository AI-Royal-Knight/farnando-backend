"""Seed a starter INEI ubigeo table.

Covers all departments, the Lima, Callao and Arequipa provinces and the districts
of Lima Metropolitana, Callao and Arequipa city. Load the full national table
with ``python manage.py load_ubigeo <csv>``. Delivery coverage is [OPEN]: only
Lima Metropolitana and Callao districts are deliverable by default.
"""

from django.db import migrations

DEPARTMENTS = [
    ("01", "Amazonas"),
    ("02", "Áncash"),
    ("03", "Apurímac"),
    ("04", "Arequipa"),
    ("05", "Ayacucho"),
    ("06", "Cajamarca"),
    ("07", "Callao"),
    ("08", "Cusco"),
    ("09", "Huancavelica"),
    ("10", "Huánuco"),
    ("11", "Ica"),
    ("12", "Junín"),
    ("13", "La Libertad"),
    ("14", "Lambayeque"),
    ("15", "Lima"),
    ("16", "Loreto"),
    ("17", "Madre de Dios"),
    ("18", "Moquegua"),
    ("19", "Pasco"),
    ("20", "Piura"),
    ("21", "Puno"),
    ("22", "San Martín"),
    ("23", "Tacna"),
    ("24", "Tumbes"),
    ("25", "Ucayali"),
]

PROVINCES = [
    ("0401", "Arequipa"),
    ("0701", "Callao"),
    ("1501", "Lima"),
]

LIMA_DISTRICTS = [
    "Lima",
    "Ancón",
    "Ate",
    "Barranco",
    "Breña",
    "Carabayllo",
    "Chaclacayo",
    "Chorrillos",
    "Cieneguilla",
    "Comas",
    "El Agustino",
    "Independencia",
    "Jesús María",
    "La Molina",
    "La Victoria",
    "Lince",
    "Los Olivos",
    "Lurigancho",
    "Lurín",
    "Magdalena del Mar",
    "Pueblo Libre",
    "Miraflores",
    "Pachacámac",
    "Pucusana",
    "Puente Piedra",
    "Punta Hermosa",
    "Punta Negra",
    "Rímac",
    "San Bartolo",
    "San Borja",
    "San Isidro",
    "San Juan de Lurigancho",
    "San Juan de Miraflores",
    "San Luis",
    "San Martín de Porres",
    "San Miguel",
    "Santa Anita",
    "Santa María del Mar",
    "Santa Rosa",
    "Santiago de Surco",
    "Surquillo",
    "Villa El Salvador",
    "Villa María del Triunfo",
]

CALLAO_DISTRICTS = [
    "Callao",
    "Bellavista",
    "Carmen de la Legua Reynoso",
    "La Perla",
    "La Punta",
    "Ventanilla",
    "Mi Perú",
]

AREQUIPA_DISTRICTS = [
    "Arequipa",
    "Alto Selva Alegre",
    "Cayma",
    "Cerro Colorado",
    "Characato",
    "Chiguata",
    "Jacobo Hunter",
    "La Joya",
    "Mariano Melgar",
    "Miraflores",
]


def seed(apps, schema_editor):
    Ubigeo = apps.get_model("patients", "Ubigeo")
    for code, name in DEPARTMENTS:
        Ubigeo.objects.update_or_create(
            code=code, defaults={"name": name, "level": "DEPARTMENT", "parent_id": None}
        )
    for code, name in PROVINCES:
        Ubigeo.objects.update_or_create(
            code=code, defaults={"name": name, "level": "PROVINCE", "parent_id": code[:2]}
        )
    groups = [
        ("1501", LIMA_DISTRICTS, True),
        ("0701", CALLAO_DISTRICTS, True),
        ("0401", AREQUIPA_DISTRICTS, False),
    ]
    for province, names, deliverable in groups:
        for index, name in enumerate(names, start=1):
            Ubigeo.objects.update_or_create(
                code=f"{province}{index:02d}",
                defaults={
                    "name": name,
                    "level": "DISTRICT",
                    "parent_id": province,
                    "is_deliverable": deliverable,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("patients", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
