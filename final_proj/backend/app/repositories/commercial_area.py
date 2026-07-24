from sqlalchemy import text
from sqlalchemy.orm import Session
from app.models.commercial_area import CommercialArea

class CommercialAreaRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_code(self, area_code: str) -> CommercialArea:
        return self.db.query(CommercialArea).filter(CommercialArea.area_code == area_code).first()

    def get_all(self, skip: int = 0, limit: int | None = None) -> list[CommercialArea]:
        query = self.db.query(CommercialArea).offset(skip)
        if limit is not None:
            query = query.limit(limit)
        return query.all()

    def search_by_name(self, keyword: str) -> list[CommercialArea]:
        return self.db.query(CommercialArea).filter(CommercialArea.area_name.like(f"%{keyword}%")).all()

    def search_summaries(self, keyword: str, limit: int = 20) -> list[dict]:
        """Return the small, current product projection used by global search."""
        escaped = str(keyword).strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if not escaped:
            return []

        rows = self.db.execute(
            text(
                """
                WITH area_context AS (
                    SELECT
                        area_code,
                        (MAX(axis_demand) + MAX(axis_accessibility)) / 2.0 AS score
                    FROM rule_location_score
                    WHERE quarter = (SELECT MAX(quarter) FROM rule_location_score)
                    GROUP BY area_code
                    HAVING MAX(axis_demand) IS NOT NULL
                       AND MAX(axis_accessibility) IS NOT NULL
                ), ranked AS (
                    SELECT
                        area_code,
                        score,
                        CUME_DIST() OVER (ORDER BY score) AS score_percentile
                    FROM area_context
                ), graded AS (
                    SELECT
                        area_code,
                        score,
                        CASE
                            WHEN score_percentile > 0.8 THEN 'A'
                            WHEN score_percentile > 0.6 THEN 'B'
                            WHEN score_percentile > 0.4 THEN 'C'
                            WHEN score_percentile > 0.2 THEN 'D'
                            ELSE 'E'
                        END AS grade,
                        CASE
                            WHEN score_percentile > 0.9 THEN 'A+'
                            WHEN score_percentile > 0.8 THEN 'A'
                            WHEN score_percentile > 0.7 THEN 'B+'
                            WHEN score_percentile > 0.6 THEN 'B'
                            WHEN score_percentile > 0.5 THEN 'C+'
                            WHEN score_percentile > 0.4 THEN 'C'
                            WHEN score_percentile > 0.3 THEN 'D+'
                            WHEN score_percentile > 0.2 THEN 'D'
                            WHEN score_percentile > 0.1 THEN 'E+'
                            ELSE 'E'
                        END AS display_grade
                    FROM ranked
                )
                SELECT
                    area.area_code,
                    area.area_name,
                    area.district_code,
                    area.latitude,
                    area.longitude,
                    summary.score,
                    summary.grade,
                    summary.display_grade
                FROM commercial_area AS area
                LEFT JOIN graded AS summary ON summary.area_code = area.area_code
                WHERE area.area_name LIKE :pattern ESCAPE '\\'
                ORDER BY
                    CASE
                        WHEN area.area_name = :keyword THEN 0
                        WHEN area.area_name LIKE :prefix ESCAPE '\\' THEN 1
                        ELSE 2
                    END,
                    summary.score DESC,
                    area.area_name ASC
                LIMIT :limit
                """
            ),
            {
                "pattern": f"%{escaped}%",
                "prefix": f"{escaped}%",
                "keyword": str(keyword).strip(),
                "limit": max(1, min(int(limit), 50)),
            },
        ).mappings().all()
        return [dict(row) for row in rows]
