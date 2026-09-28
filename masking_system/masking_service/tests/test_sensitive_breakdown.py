from app.webapp.sensitive_breakdown import summarize_sensitive_breakdown


def test_corporate_rules_are_grouped_by_title_without_hashes():
    rows = summarize_sensitive_breakdown(
        {
            "kurumsal_terim_i_denetim_c097f4235ba4": 9,
            "kurumsal_terim_i_denetim_553bd53a98a9": 7,
            "kurumsal_terim_i_denetim_cf90a8a9b659": 7,
            "llm_dynamic": 19,
            "generic_secret_assignment": 4,
            "contextual_personnel_id": 1,
        }
    )

    assert [row.finding_count for row in rows] == [23, 19, 4, 1]
    corporate = rows[0]
    assert corporate.data_type == "Kurumsal ifade"
    assert corporate.title == "I Denetim"
    assert corporate.distinct_terms == 3
    assert "kurumsal_terim" not in str([row.as_table_row() for row in rows])
    assert "c097f4235ba4" not in str([row.as_table_row() for row in rows])


def test_known_and_unknown_rules_are_human_readable():
    rows = summarize_sensitive_breakdown(
        {
            "contextual_personnel_id": 2,
            "presidio_internal_prod_hostname": 3,
            "future_sensitive_value": 1,
            "llm:IC_SERVIS_ADI": 4,
        }
    )

    labels = {row.data_type for row in rows}
    assert "Personel / sicil bilgisi (bağlamsal)" in labels
    assert "Kurum içi sunucu adı" in labels
    assert "Future sensitive value" in labels
    assert "Yapay zekâ — IC SERVIS ADI" in labels
    assert all("_" not in label for label in labels)


def test_zero_count_entries_are_not_rendered():
    assert summarize_sensitive_breakdown({"email_address": 0}) == []
