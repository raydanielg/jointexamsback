"""Convert builder output into generic sections: [{title, columns, rows}]
so renderers stay type-agnostic."""


def _num(v):
    if v is None:
        return ""
    return str(v)


def tabularize(data, report_type):
    exam = data["exam"]
    sections = []

    if report_type in ("CANDIDATE_RESULT",):
        for cand in data["candidates"]:
            r = cand["result"]
            rows = [
                [
                    s.exam_subject.subject.name,
                    _num(s.score), _num(s.max_score), _num(s.percentage),
                    s.grade, s.position or "", s.status,
                ]
                for s in cand["subjects"]
            ]
            sections.append({
                "title": f"{r.exam_candidate.candidate.full_name} "
                         f"({r.exam_candidate.candidate_number}) — {r.exam_candidate.school.school_name}",
                "meta": {
                    "position": r.position, "school_position": r.school_position,
                    "average": _num(r.average_percentage), "grade": r.grade,
                    "division": r.division, "status": r.status,
                },
                "columns": ["Subject", "Score", "Max", "%", "Grade", "Position", "Status"],
                "rows": rows,
            })

    elif report_type == "RESULT_SLIP":
        r = data["result"]
        rows = [
            [s.exam_subject.subject.name, _num(s.score), _num(s.max_score),
             _num(s.percentage), s.grade, s.position or "", s.status]
            for s in data["subjects"]
        ]
        sections.append({
            "title": f"{r.exam_candidate.candidate.full_name} ({r.exam_candidate.candidate_number})",
            "meta": {
                "school": data["school"].school_name, "position": r.position,
                "school_position": r.school_position, "average": _num(r.average_percentage),
                "grade": r.grade, "division": r.division, "status": r.status,
            },
            "columns": ["Subject", "Score", "Max", "%", "Grade", "Position", "Status"],
            "rows": rows,
        })

    elif report_type == "FULL_RESULTS":
        headers = ["No.", "Candidate", "School"] + [es.subject.code for es in data["subjects"]] + ["Avg", "Grade", "Pos"]
        rows = []
        for row in data["rows"]:
            r = row["result"]
            cells = [
                _num(c.percentage) if c and c.percentage is not None else (c.status if c else "-")
                for c in row["subject_cells"]
            ]
            rows.append([
                r.exam_candidate.candidate_number,
                r.exam_candidate.candidate.full_name,
                r.exam_candidate.school.school_code,
                *cells,
                _num(r.average_percentage), r.grade, r.position or "",
            ])
        sections.append({"title": data["title"], "columns": headers, "rows": rows})

    elif report_type in ("SUBJECT_RESULTS", "SUBJECT_POSITIONS"):
        for subject, items in data["groups"].items():
            rows = [
                [
                    s.exam_candidate.candidate_number,
                    s.exam_candidate.candidate.full_name,
                    s.exam_candidate.school.school_name,
                    _num(s.percentage), s.grade, s.position or "",
                    s.school_position or "", s.status,
                ]
                for s in items
            ]
            sections.append({
                "title": subject,
                "columns": ["No.", "Candidate", "School", "%", "Grade", "Position", "School Pos.", "Status"],
                "rows": rows,
            })

    elif report_type == "MARK_SHEET":
        headers = ["No.", "Candidate", "School"] + [c.code for c in data["components"]] + ["Total"]
        rows = []
        for row in data["rows"]:
            e = row["enrollment"]
            cells = [_num(m.value) if m and m.value is not None else "-" for m in row["marks"]]
            total = sum(float(m.value) for m in row["marks"] if m and m.value is not None)
            rows.append([e.candidate_number, e.candidate.full_name, e.school.school_code, *cells, round(total, 2)])
        sections.append({"title": data["title"], "columns": headers, "rows": rows})

    elif report_type == "SCHOOL_RESULTS":
        for school, results in data["groups"]:
            rows = [
                [r.exam_candidate.candidate_number, r.exam_candidate.candidate.full_name,
                 _num(r.average_percentage), r.grade, r.division, r.position or "",
                 r.school_position or "", r.status]
                for r in results
            ]
            sections.append({
                "title": f"{school.school_name} ({school.school_code})",
                "columns": ["No.", "Candidate", "Avg %", "Grade", "Div", "Position", "School Pos.", "Status"],
                "rows": rows,
            })

    elif report_type == "POSITION_REPORT":
        rows = [
            [r.position, r.exam_candidate.candidate_number,
             r.exam_candidate.candidate.full_name, r.exam_candidate.school.school_name,
             _num(r.average_percentage), r.grade, r.division]
            for r in data["rows"]
        ]
        sections.append({
            "title": data["title"],
            "columns": ["Position", "No.", "Candidate", "School", "Avg %", "Grade", "Division"],
            "rows": rows,
        })

    elif report_type == "SCHOOL_SUMMARY":
        rows = [
            [s["school"].school_name, s["candidates"], s["scored"],
             _num(s["average"]), _num(s["pass_rate"]), s["absent"], s["incomplete"]]
            for s in data["summaries"]
        ]
        sections.append({
            "title": data["title"],
            "columns": ["School", "Candidates", "Scored", "Average %", "Pass %", "Absent", "Incomplete"],
            "rows": rows,
        })

    elif report_type == "EXAM_SUMMARY":
        stats = data["stats"]
        rows = [
            ["Status", stats["examination"]["status"]],
            ["Candidates", stats["candidates"]["total"]],
            ["Active candidates", stats["candidates"]["active"]],
            ["Absent", stats["candidates"]["absent"]],
            ["Schools", stats["schools"]],
            ["Subjects", stats["subjects"]],
            ["Marks completion %", stats["marks_completion_percent"]],
            ["Average %", _num(stats["average_percentage"])],
            ["Highest avg", _num(stats["highest_average"])],
            ["Lowest avg", _num(stats["lowest_average"])],
        ]
        for grade, n in sorted(stats["grade_distribution"].items()):
            rows.append([f"Grade {grade}", n])
        sections.append({"title": data["title"], "columns": ["Metric", "Value"], "rows": rows})

    elif report_type == "GRADE_DISTRIBUTION":
        rows = [[g["grade"], g["count"]] for g in data["overall"]]
        sections.append({"title": "Overall", "columns": ["Grade", "Count"], "rows": rows})
        if data["by_school"]:
            rows2 = [[g["exam_candidate__school__school_name"], g["grade"], g["count"]]
                     for g in data["by_school"]]
            sections.append({"title": "By School", "columns": ["School", "Grade", "Count"], "rows": rows2})

    elif report_type == "PERFORMANCE_ANALYSIS":
        a = data["analytics"]
        rows = [[s["subject"], s["candidates"], _num(s["average"]), _num(s["highest"]),
                 _num(s["lowest"]), _num(s["pass_rate"])] for s in a["subjects"]]
        sections.append({"title": "Subject Performance",
                         "columns": ["Subject", "Candidates", "Avg %", "High", "Low", "Pass %"],
                         "rows": rows})
        rows2 = [[s["school"], s["candidates"], s["scored"], _num(s["average"]),
                  _num(s["pass_rate"])] for s in a["schools"]]
        sections.append({"title": "School Performance",
                         "columns": ["School", "Candidates", "Scored", "Avg %", "Pass %"],
                         "rows": rows2})

    elif report_type == "MISSING_MARKS":
        rows = [
            [
                r["candidate_number"] if isinstance(r, dict) else r.exam_candidate.candidate_number,
                r["candidate_name"] if isinstance(r, dict) else r.exam_candidate.candidate.full_name,
                r["school"] if isinstance(r, dict) else r.exam_candidate.school.school_name,
                r["subject"] if isinstance(r, dict) else r.component.exam_subject.subject.name,
                r["component"] if isinstance(r, dict) else r.component.code,
                r["status"] if isinstance(r, dict) else r.status,
            ]
            for r in data["rows"]
        ]
        sections.append({"title": data["title"],
                         "columns": ["No.", "Candidate", "School", "Subject", "Component", "Status"],
                         "rows": rows})

    elif report_type == "ABSENT_CANDIDATES":
        rows = [
            [r.exam_candidate.candidate_number, r.exam_candidate.candidate.full_name,
             r.exam_candidate.school.school_name]
            for r in data["rows"]
        ]
        sections.append({"title": data["title"],
                         "columns": ["No.", "Candidate", "School"], "rows": rows})

    elif report_type == "MARKS_COMPLETION":
        rows = [[r["subject"], r["entered"], r["expected"], r["percent"]] for r in data["rows"]]
        sections.append({"title": data["title"],
                         "columns": ["Subject", "Entered", "Expected", "%"], "rows": rows})

    elif report_type == "AUDIT":
        rows = [[r["action"], r["actor__email"], r["created_at"].isoformat() if r["created_at"] else ""]
                for r in data["rows"]]
        sections.append({"title": data["title"],
                         "columns": ["Action", "Actor", "Timestamp"], "rows": rows})

    return {
        "title": data["title"],
        "exam_name": exam.name,
        "exam_code": exam.code,
        "sections": sections,
    }
