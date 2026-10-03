"""Seed the full Tanzanian curriculum subjects (primary, O-level, A-level)
as GLOBAL subjects — every organization can pick them for its examinations.
Idempotent: safe to run repeatedly."""
from django.core.management.base import BaseCommand

from apps.subjects.models import Subject

# (name, code, short_name, level tag)
SUBJECTS = [
    # ---- Primary (NECTA PSLE) ----
    ("Kiswahili", "KISW-P", "Kiswahili", "primary"),
    ("English Language", "ENG-P", "English", "primary"),
    ("Mathematics", "MATH-P", "Maths", "primary"),
    ("Science and Technology", "SCI-P", "Science", "primary"),
    ("Social Studies", "SST-P", "Social", "primary"),
    ("Civic and Moral Education", "CIV-P", "Civics", "primary"),
    ("Information and Communication Technology", "ICT-P", "ICT", "primary"),
    ("Physical Education and Arts", "PE-P", "P.E.", "primary"),

    # ---- O-Level (NECTA CSEE) ----
    ("Kiswahili", "KISW", "Kiswahili", "o-level"),
    ("English Language", "ENG", "English", "o-level"),
    ("Basic Mathematics", "BMATH", "B. Maths", "o-level"),
    ("Physics", "PHY", "Physics", "o-level"),
    ("Chemistry", "CHEM", "Chemistry", "o-level"),
    ("Biology", "BIO", "Biology", "o-level"),
    ("Geography", "GEO", "Geography", "o-level"),
    ("History", "HIST", "History", "o-level"),
    ("Civics", "CIV", "Civics", "o-level"),
    ("Commerce", "COMM", "Commerce", "o-level"),
    ("Bookkeeping", "BK", "Bookkeeping", "o-level"),
    ("Computer Studies", "COMP", "Computer", "o-level"),
    ("Literature in English", "LIT", "Literature", "o-level"),
    ("French", "FRN", "French", "o-level"),
    ("Arabic", "ARA", "Arabic", "o-level"),
    ("Agriculture", "AGRIC", "Agriculture", "o-level"),
    ("Engineering Science", "ENGSCI", "Eng. Sci.", "o-level"),
    ("Food and Nutrition", "FOOD", "Food", "o-level"),
    ("Physical Education", "PE", "P.E.", "o-level"),
    ("Fine Art", "ART", "Fine Art", "o-level"),
    ("Music", "MUS", "Music", "o-level"),
    ("Textiles and Design", "TEXT", "Textiles", "o-level"),

    # ---- A-Level (NECTA ACSEE) ----
    ("General Studies", "GS", "G.S.", "a-level"),
    ("Advanced Mathematics", "ADVMATH", "Adv. Maths", "a-level"),
    ("Basic Applied Mathematics", "BAM", "B.A.M.", "a-level"),
    ("Advanced Physics", "PHY-A", "Physics", "a-level"),
    ("Advanced Chemistry", "CHEM-A", "Chemistry", "a-level"),
    ("Advanced Biology", "BIO-A", "Biology", "a-level"),
    ("Advanced Geography", "GEO-A", "Geography", "a-level"),
    ("Advanced History", "HIST-A", "History", "a-level"),
    ("Advanced Kiswahili", "KISW-A", "Kiswahili", "a-level"),
    ("Advanced English", "ENG-A", "English", "a-level"),
    ("Economics", "ECON", "Economics", "a-level"),
    ("Accountancy", "ACCT", "Accountancy", "a-level"),
    ("Advanced Commerce", "COMM-A", "Commerce", "a-level"),
    ("Agriculture Science", "AGRIC-A", "Agriculture", "a-level"),
    ("Computer Science", "CS-A", "Comp. Sci.", "a-level"),
    ("Divinity", "DIV", "Divinity", "a-level"),
    ("Fine Art and Crafts", "ART-A", "Fine Art", "a-level"),
    ("Chinese", "CHN", "Chinese", "a-level"),
]

LEVEL_LABEL = {"primary": "Primary", "o-level": "O-Level", "a-level": "A-Level"}


class Command(BaseCommand):
    help = "Seed Tanzanian curriculum subjects as global shared subjects."

    def handle(self, *args, **options):
        created, existing = 0, 0
        for name, code, short_name, level in SUBJECTS:
            _, was_created = Subject.objects.get_or_create(
                code=code,
                school=None,
                defaults={
                    "name": name,
                    "short_name": short_name,
                    "description": f"{name} — {LEVEL_LABEL[level]} (NECTA curriculum)",
                },
            )
            if was_created:
                created += 1
            else:
                existing += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Subjects seeded: {created} created, {existing} already existed."
            )
        )
