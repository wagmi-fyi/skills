#!/usr/bin/env python3
"""
Shared Rule Matching Module
Contains reusable functions for testing categorization rules.
Used by: apply_cat_rules, test_cat_rule
"""


def extract_field(import_record: dict, field_name: str) -> any:
    """
    Extract field value from import record.

    Args:
        import_record: Dict with keys: id, source, amount, banking_date, raw_data
        field_name: One of: "reference", "amount", "source", "any_text"

    Returns:
        Field value (type depends on field)

    Raises:
        ValueError: If field_name is unknown
    """
    if field_name == "reference":
        return import_record['raw_data'].get('Reference', '')
    elif field_name == "amount":
        return import_record['amount']
    elif field_name == "source":
        return import_record['source']
    elif field_name == "any_text":
        reference = import_record['raw_data'].get('Reference', '')
        source = import_record['source']
        return f"{reference} {source}"
    else:
        raise ValueError(f"Unknown field: {field_name}")


def evaluate_condition(field_value: any, operator: str, expected_value: any) -> bool:
    """
    Evaluate a single condition.

    Args:
        field_value: Actual value from import record
        operator: Comparison operator (equals, contains, starts_with, is_blank, less_than, greater_than, equals_number)
        expected_value: Expected value to compare against

    Returns:
        True if condition matches, False otherwise

    Raises:
        ValueError: If operator is unknown
    """
    if operator == "equals":
        return field_value == expected_value
    elif operator == "contains":
        return expected_value.lower() in str(field_value).lower()
    elif operator == "starts_with":
        return str(field_value).lower().startswith(expected_value.lower())
    elif operator == "is_blank":
        return not field_value or str(field_value).strip() == ""
    elif operator == "less_than":
        return int(field_value) < int(expected_value)
    elif operator == "greater_than":
        return int(field_value) > int(expected_value)
    elif operator == "equals_number":
        return int(field_value) == int(expected_value)
    else:
        raise ValueError(f"Unknown operator: {operator}")


def match_rule(import_record: dict, rule: dict) -> bool:
    """
    Check if import matches rule's conditions.

    Args:
        import_record: Dict with keys: id, source, amount, banking_date, raw_data
        rule: Dict with keys: id, priority, name, match_criteria, apply_actions

    Returns:
        True if import matches rule, False otherwise

    Raises:
        ValueError: If rule logic is invalid or field/operator unknown
    """
    logic = rule['match_criteria']['logic']
    conditions = rule['match_criteria']['conditions']

    matches = []

    for condition in conditions:
        field_value = extract_field(import_record, condition['field'])
        operator = condition['operator']
        expected_value = condition.get('value')

        match = evaluate_condition(field_value, operator, expected_value)
        matches.append(match)

    if logic == "all":
        return all(matches)  # AND logic
    elif logic == "any":
        return any(matches)  # OR logic
    else:
        raise ValueError(f"Invalid logic: {logic}")
