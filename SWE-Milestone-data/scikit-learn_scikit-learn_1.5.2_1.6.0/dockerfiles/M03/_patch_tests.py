#!/usr/bin/env python3
"""
Patch test files for M03 compatibility.
This script removes or comments out tests that use APIs not available in M03.
"""

import re
import os
import sys

def comment_out_entire_file(filepath, reason):
    """Comment out an entire file by adding skip marker."""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    with open(filepath, 'r') as f:
        lines = f.readlines()

    with open(filepath, 'w') as f:
        f.write(f"# [ENV-PATCH] {reason}\n")
        f.write("# This file is skipped because it imports modules not available in M03\n")
        f.write("import pytest\n")
        f.write("pytestmark = pytest.mark.skip(reason='[ENV-PATCH] Entire file skipped - imports unavailable in M03')\n\n")
        for line in lines:
            if line.strip():
                f.write(f"# {line}")
            else:
                f.write(line)

    print(f"Commented out entire file: {filepath}")


def patch_file(filepath, patches):
    """Apply patches to a file."""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    with open(filepath, 'r') as f:
        content = f.read()

    original = content

    for patch in patches:
        if patch['type'] == 'remove_import':
            # Remove import lines
            pattern = re.compile(patch['pattern'], re.MULTILINE)
            content = pattern.sub('', content)

        elif patch['type'] == 'skip_test':
            # Add @pytest.mark.skip decorator before a test function
            test_name = patch['test_name']
            reason = patch.get('reason', 'API not available in M03')
            pattern = re.compile(rf'^(def {test_name}\()', re.MULTILINE)
            replacement = f'@pytest.mark.skip(reason="[ENV-PATCH] {reason}")\n\\1'
            content = pattern.sub(replacement, content)

        elif patch['type'] == 'replace_line':
            # Replace a specific line pattern
            pattern = re.compile(patch['pattern'], re.MULTILINE)
            content = pattern.sub(patch['replacement'], content)

        elif patch['type'] == 'comment_usage':
            # Comment out all lines using a specific identifier
            pattern = re.compile(patch['pattern'])
            lines = content.split('\n')
            new_lines = []
            for line in lines:
                if pattern.search(line) and not line.strip().startswith('#'):
                    new_lines.append(f"# [ENV-PATCH] {line}")
                else:
                    new_lines.append(line)
            content = '\n'.join(new_lines)

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Patched: {filepath}")
    else:
        print(f"No changes: {filepath}")


def patch_end_state():
    """Apply patches for END state - all milestone features available."""
    base_path = '/testbed'

    # Files that need to be entirely skipped because they import non-existent modules
    files_to_skip = [
        ('sklearn/utils/tests/test_tags.py', 'get_tags function not available in M03'),
        ('sklearn/utils/tests/test_unique.py', 'sklearn.utils._unique module not available in M03'),
        ('sklearn/utils/tests/test_cython_blas.py', 'ColMajor/NoTrans not available in M03'),
        ('sklearn/utils/tests/test_array_api.py', '_count_nonzero not available in M03'),
        ('sklearn/utils/tests/test_testing.py', 'assert_docstring_consistency not available in M03'),
        ('sklearn/utils/tests/test_estimator_checks.py', 'get_tags/default_tags not available in M03'),
        ('sklearn/utils/tests/test_validation.py', 'get_tags not available in M03'),
        ('sklearn/tests/test_common.py', 'get_tags not available in M03'),
        ('sklearn/tests/test_docstring_parameters.py', 'assert_docstring_consistency not available in M03'),
        ('sklearn/tree/tests/test_tree.py', '_build_pruned_tree_py not available in M03'),
        ('sklearn/model_selection/tests/test_search.py', '_yield_masked_array_for_each_param not available in M03'),
        ('sklearn/metrics/tests/test_dist_metrics.py', 'DEPRECATED_METRICS not available'),
        ('sklearn/cluster/tests/test_hierarchical.py', 'Imports from test_dist_metrics'),
        ('sklearn/cluster/tests/test_k_means.py', 'get_tags/_test_common not available'),
        ('sklearn/decomposition/tests/test_pca.py', 'get_tags/_test_common not available'),
        ('sklearn/feature_selection/tests/test_base.py', 'get_tags/_test_common not available'),
        ('sklearn/feature_selection/tests/test_rfe.py', 'get_tags/_test_common not available'),
        ('sklearn/inspection/tests/test_partial_dependence.py', 'get_tags/_test_common not available'),
        ('sklearn/linear_model/tests/test_ridge.py', 'get_tags/_test_common not available'),
        ('sklearn/metrics/tests/test_pairwise_distances_reduction.py', 'get_tags/_test_common not available'),
        ('sklearn/model_selection/tests/test_successive_halving.py', 'get_tags/_test_common not available'),
        ('sklearn/model_selection/tests/test_validation.py', 'get_tags/_test_common not available'),
        ('sklearn/neighbors/tests/test_nca.py', 'get_tags/_test_common not available'),
        ('sklearn/neighbors/tests/test_neighbors.py', 'get_tags/_test_common not available'),
        ('sklearn/preprocessing/tests/test_data.py', 'get_tags/_test_common not available'),
        ('sklearn/semi_supervised/tests/test_self_training.py', 'get_tags/_test_common not available'),
    ]

    for filepath, reason in files_to_skip:
        comment_out_entire_file(f'{base_path}/{filepath}', reason)

    # Patch sklearn/tests/test_base.py for END state
    patch_file(f'{base_path}/sklearn/tests/test_base.py', [
        {'type': 'remove_import', 'pattern': r'^from sklearn\.utils\.validation import _check_n_features, validate_data\n'},
        {'type': 'skip_test', 'test_name': 'test_n_features_in_validation', 'reason': '_check_n_features not available in M03'},
        {'type': 'skip_test', 'test_name': 'test_n_features_in_no_validation', 'reason': '_check_n_features not available in M03'},
        {'type': 'skip_test', 'test_name': 'test_feature_names_in', 'reason': 'validate_data not available in M03'},
        {'type': 'skip_test', 'test_name': 'test_validate_data_skip_check_array', 'reason': 'validate_data not available in M03'},
        {'type': 'skip_test', 'test_name': 'test_validate_data_cast_to_ndarray', 'reason': 'validate_data not available in M03'},
    ])

    # Patch sklearn/tests/test_pipeline.py
    patch_file(f'{base_path}/sklearn/tests/test_pipeline.py', [
        {'type': 'replace_line',
         'pattern': r'^from sklearn\.utils\.validation import _check_feature_names, check_is_fitted$',
         'replacement': 'from sklearn.utils.validation import check_is_fitted'},
        {'type': 'comment_usage', 'pattern': r'_check_feature_names'},
    ])


def patch_start_state():
    """Apply patches for START state - milestone features NOT available yet."""
    base_path = '/testbed'

    # Same files as END state need to be skipped
    files_to_skip = [
        ('sklearn/utils/tests/test_tags.py', 'get_tags function not available in M03'),
        ('sklearn/utils/tests/test_unique.py', 'sklearn.utils._unique module not available in M03'),
        ('sklearn/utils/tests/test_cython_blas.py', 'ColMajor/NoTrans not available in M03'),
        ('sklearn/utils/tests/test_array_api.py', '_count_nonzero not available in M03'),
        ('sklearn/utils/tests/test_testing.py', 'assert_docstring_consistency not available in M03'),
        ('sklearn/utils/tests/test_estimator_checks.py', 'get_tags/default_tags not available in M03'),
        ('sklearn/utils/tests/test_validation.py', 'get_tags not available in M03'),
        ('sklearn/tests/test_common.py', 'get_tags not available in M03'),
        ('sklearn/tests/test_docstring_parameters.py', 'assert_docstring_consistency not available in M03'),
        ('sklearn/tree/tests/test_tree.py', '_build_pruned_tree_py not available in M03'),
        ('sklearn/model_selection/tests/test_search.py', '_yield_masked_array_for_each_param not available in M03'),
        ('sklearn/metrics/tests/test_dist_metrics.py', 'DEPRECATED_METRICS not available'),
        ('sklearn/cluster/tests/test_hierarchical.py', 'Imports from test_dist_metrics'),
        ('sklearn/cluster/tests/test_k_means.py', 'get_tags/_test_common not available'),
        ('sklearn/decomposition/tests/test_pca.py', 'get_tags/_test_common not available'),
        ('sklearn/feature_selection/tests/test_base.py', 'get_tags/_test_common not available'),
        ('sklearn/feature_selection/tests/test_rfe.py', 'get_tags/_test_common not available'),
        ('sklearn/inspection/tests/test_partial_dependence.py', 'get_tags/_test_common not available'),
        ('sklearn/linear_model/tests/test_ridge.py', 'get_tags/_test_common not available'),
        ('sklearn/metrics/tests/test_pairwise_distances_reduction.py', 'get_tags/_test_common not available'),
        ('sklearn/model_selection/tests/test_successive_halving.py', 'get_tags/_test_common not available'),
        ('sklearn/model_selection/tests/test_validation.py', 'get_tags/_test_common not available'),
        ('sklearn/neighbors/tests/test_nca.py', 'get_tags/_test_common not available'),
        ('sklearn/neighbors/tests/test_neighbors.py', 'get_tags/_test_common not available'),
        ('sklearn/preprocessing/tests/test_data.py', 'get_tags/_test_common not available'),
        ('sklearn/semi_supervised/tests/test_self_training.py', 'get_tags/_test_common not available'),
    ]

    for filepath, reason in files_to_skip:
        comment_out_entire_file(f'{base_path}/{filepath}', reason)

    # Patch sklearn/tests/test_base.py for START state
    # Remove is_clusterer and is_regressor imports since they're milestone additions
    patch_file(f'{base_path}/sklearn/tests/test_base.py', [
        {'type': 'remove_import', 'pattern': r'^from sklearn\.utils\.validation import _check_n_features, validate_data\n'},
        # Remove milestone-specific imports (is_clusterer, is_regressor)
        {'type': 'replace_line',
         'pattern': r'^from sklearn\.base import \(\n    BaseEstimator,\n    OutlierMixin,\n    TransformerMixin,\n    clone,\n    is_classifier,\n    is_clusterer,\n    is_regressor,\n\)',
         'replacement': 'from sklearn.base import (\n    BaseEstimator,\n    OutlierMixin,\n    TransformerMixin,\n    clone,\n    is_classifier,\n)'},
        # Skip tests for milestone features
        {'type': 'skip_test', 'test_name': 'test_is_clusterer', 'reason': 'is_clusterer not available at START state'},
        {'type': 'skip_test', 'test_name': 'test_is_regressor', 'reason': 'is_regressor tests added at END state'},
        {'type': 'skip_test', 'test_name': 'test_n_features_in_validation', 'reason': '_check_n_features not available'},
        {'type': 'skip_test', 'test_name': 'test_n_features_in_no_validation', 'reason': '_check_n_features not available'},
        {'type': 'skip_test', 'test_name': 'test_feature_names_in', 'reason': 'validate_data not available'},
        {'type': 'skip_test', 'test_name': 'test_validate_data_skip_check_array', 'reason': 'validate_data not available'},
        {'type': 'skip_test', 'test_name': 'test_validate_data_cast_to_ndarray', 'reason': 'validate_data not available'},
    ])

    # Patch sklearn/tests/test_pipeline.py
    patch_file(f'{base_path}/sklearn/tests/test_pipeline.py', [
        {'type': 'replace_line',
         'pattern': r'^from sklearn\.utils\.validation import _check_feature_names, check_is_fitted$',
         'replacement': 'from sklearn.utils.validation import check_is_fitted'},
        {'type': 'comment_usage', 'pattern': r'_check_feature_names'},
        # Skip tests for pipeline fitted check (milestone commit 4dfbfb9)
        {'type': 'skip_test', 'test_name': 'test_pipeline_warns_not_fitted', 'reason': 'Pipeline fitted check not available at START state'},
    ])


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'start':
        print("Patching for START state...")
        patch_start_state()
    else:
        print("Patching for END state...")
        patch_end_state()


if __name__ == '__main__':
    main()
