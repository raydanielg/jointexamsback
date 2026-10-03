from .models import RankingConfiguration


def rank_items(items, score_getter, method=RankingConfiguration.Method.COMPETITION,
               include_unscored=False):
    """Assign positions to a list of objects with a comparable score.

    Returns a list of (item, position-or-None). Descending order, ties per
    configured method. Items with score None get no position unless
    ``include_unscored`` places them at the bottom.
    """
    scored = [(item, score_getter(item)) for item in items]
    ranked = [(item, s) for item, s in scored if s is not None]
    unscored = [item for item, s in scored if s is None]

    ranked.sort(key=lambda t: t[1], reverse=True)

    positions = {}
    if method == RankingConfiguration.Method.DENSE:
        position = 0
        last_score = object()
        for item, score in ranked:
            if score != last_score:
                position += 1
                last_score = score
            positions[id(item)] = position
    elif method == RankingConfiguration.Method.ORDINAL:
        for i, (item, score) in enumerate(ranked, start=1):
            positions[id(item)] = i
    else:  # COMPETITION
        for i, (item, score) in enumerate(ranked):
            if i == 0 or score != ranked[i - 1][1]:
                positions[id(item)] = i + 1
            else:
                positions[id(item)] = positions[id(ranked[i - 1][0])]

    order = {id(item): i for i, (item, _) in enumerate(scored)}
    result = [(item, positions.get(id(item))) for item, score in scored if score is not None]
    if include_unscored:
        bottom = (len(positions.values()) and max(positions.values()) or 0) + 1
        for item in unscored:
            result.append((item, bottom))
    result.sort(key=lambda t: order[id(t[0])])
    return result


class RankingService:
    """Assigns positions onto freshly-calculated (or persisted) result
    objects. The caller bulk-saves afterwards."""

    @staticmethod
    def _config(examination):
        if examination.ranking_config:
            return examination.ranking_config
        return RankingConfiguration.objects.filter(
            school=examination.school, is_default=True
        ).first() or RankingConfiguration(method=RankingConfiguration.Method.COMPETITION)

    @staticmethod
    def assign_subject_positions(examination, exam_subject, results):
        config = RankingService._config(examination)
        ranked = rank_items(results, lambda r: r.percentage, config.method)
        for r, pos in ranked:
            r.position = pos
        # School positions per school group
        groups = {}
        for r in results:
            groups.setdefault(r.exam_candidate.school_id, []).append(r)
        for group in groups.values():
            for r, pos in rank_items(group, lambda x: x.percentage, config.method):
                r.school_position = pos
        return results

    @staticmethod
    def assign_exam_positions(examination, results):
        config = RankingService._config(examination)
        key = (
            (lambda r: r.average_percentage)
            if config.rank_by == "average_percentage"
            else (lambda r: r.total_score)
        )
        ranked = rank_items(results, key, config.method, include_unscored=config.include_absent)
        for r, pos in ranked:
            r.position = pos
        groups = {}
        for r in results:
            groups.setdefault(r.exam_candidate.school_id, []).append(r)
        for group in groups.values():
            for r, pos in rank_items(group, key, config.method):
                r.school_position = pos
        list_groups = {}
        for r in results:
            if r.exam_candidate.source_list_id:
                list_groups.setdefault(r.exam_candidate.source_list_id, []).append(r)
        for group in list_groups.values():
            for r, pos in rank_items(group, key, config.method):
                r.list_position = pos
        return results

    @staticmethod
    def school_standings(examination, results):
        """School-level ordering by mean candidate average percentage."""
        config = RankingService._config(examination)
        from apps.analytics.services import _pass_grades

        pass_grades = set(_pass_grades(examination))
        groups = {}
        for r in results:
            groups.setdefault(r.exam_candidate.school_id, []).append(r)
        stats = []
        for school_id, group in groups.items():
            scored = [g for g in group if g.average_percentage is not None]
            avg = (
                sum(g.average_percentage for g in scored) / len(scored)
                if scored else None
            )
            passed = len([g for g in scored if g.grade in pass_grades]) if scored else 0
            stats.append(
                {
                    "school_id": school_id,
                    "candidates": len(group),
                    "average": round(float(avg), 2) if avg is not None else None,
                    "pass_rate": round(passed / len(scored) * 100, 1) if scored else None,
                }
            )
        ranked = rank_items(stats, lambda s: s["average"], config.method)
        for s, pos in ranked:
            s["position"] = pos
        return sorted(stats, key=lambda s: (s["position"] is None, s["position"] or 0))
