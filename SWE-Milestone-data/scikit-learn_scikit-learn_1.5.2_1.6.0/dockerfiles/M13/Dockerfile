# Build on pre-configured base image
FROM scikit-learn_scikit-learn_1.5.2_1.6.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# Configure git identity for commits
RUN cd /testbed && \
    git config user.email "env-patch@localhost" && \
    git config user.name "ENV-PATCH"

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M13-end

# Create a Python patch script for M13 that properly handles all import errors
RUN cat > /tmp/patch_m13_end.py << 'EOFPATCH'
#!/usr/bin/env python3
"""
Patch test files for M13 END state compatibility.
In END state, assert_docstring_consistency is available.
"""
import re
import os

def patch_file(filepath, patches):
    """Apply string replacement patches to a file."""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return False

    with open(filepath, 'r') as f:
        content = f.read()

    original = content
    for old, new in patches:
        content = content.replace(old, new)

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Patched: {filepath}")
        return True
    else:
        print(f"No changes needed: {filepath}")
        return False


def skip_entire_file(filepath, reason):
    """
    Skip an entire file by commenting out all its content and adding a skip marker.
    This prevents import errors since the imports won't be executed.
    """
    if not os.path.exists(filepath):
        print(f"File not found (expected): {filepath}")
        return

    with open(filepath, 'r') as f:
        lines = f.readlines()

    # Check if already patched
    if lines and '[ENV-PATCH]' in lines[0]:
        print(f"Already patched: {filepath}")
        return

    with open(filepath, 'w') as f:
        f.write(f'# [ENV-PATCH] {reason}\n')
        f.write('# This file is skipped because it imports modules not available\n')
        f.write('import pytest\n')
        f.write(f'pytestmark = pytest.mark.skip(reason="[ENV-PATCH] {reason}")\n\n')
        # Comment out all existing lines
        for line in lines:
            if line.strip():
                f.write(f"# {line}")
            else:
                f.write(line)

    print(f"Patched (skip entire file): {filepath}")


def patch_test_docstring_parameters_end():
    """
    Patch test_docstring_parameters.py for END state.
    Only wrap _construct_instances in try-except since assert_docstring_consistency IS available.
    """
    filepath = '/testbed/sklearn/tests/test_docstring_parameters.py'

    patches = [
        # Replace direct import with try-except wrapper
        (
            'from sklearn.utils._test_common.instance_generator import _construct_instances',
            '''try:
    from sklearn.utils._test_common.instance_generator import _construct_instances
except ImportError:
    # _test_common module not available in M13
    def _construct_instances(Estimator):
        raise ImportError("_construct_instances not available in M13")'''
        ),
    ]

    patch_file(filepath, patches)


def patch_files_without_target_tests():
    """Skip files that have import errors but don't contain target tests."""

    files_to_skip = [
        ('/testbed/sklearn/frozen/tests/test_frozen.py', 'FrozenEstimator/is_clusterer not available in M13'),
        ('/testbed/sklearn/tests/test_metaestimators.py', '_test_common module not available in M13'),
        ('/testbed/sklearn/utils/tests/test_plotting.py', '_despine not available in M13'),
        ('/testbed/sklearn/tests/test_calibration.py', 'FrozenEstimator not available in M13'),
        ('/testbed/sklearn/tests/test_base.py', 'Multiple APIs not available in M13'),
        ('/testbed/sklearn/utils/tests/test_tags.py', 'get_tags function not available in M13'),
        ('/testbed/sklearn/utils/tests/test_unique.py', 'sklearn.utils._unique module not available in M13'),
        ('/testbed/sklearn/utils/tests/test_cython_blas.py', 'ColMajor/NoTrans not available in M13'),
        ('/testbed/sklearn/utils/tests/test_array_api.py', '_count_nonzero not available in M13'),
        ('/testbed/sklearn/utils/tests/test_estimator_checks.py', 'get_tags/default_tags not available in M13'),
        ('/testbed/sklearn/utils/tests/test_validation.py', 'get_tags not available in M13'),
        ('/testbed/sklearn/tests/test_common.py', 'get_tags not available in M13'),
        ('/testbed/sklearn/tree/tests/test_tree.py', '_build_pruned_tree_py not available in M13'),
        ('/testbed/sklearn/model_selection/tests/test_search.py', '_yield_masked_array_for_each_param not available in M13'),
        ('/testbed/sklearn/metrics/tests/test_dist_metrics.py', 'DEPRECATED_METRICS not available'),
        ('/testbed/sklearn/cluster/tests/test_hierarchical.py', 'DEPRECATED_METRICS from test_dist_metrics'),
        ('/testbed/sklearn/cluster/tests/test_k_means.py', '_get_threadpool_controller/_test_common not available'),
        ('/testbed/sklearn/decomposition/tests/test_pca.py', '_test_common not available'),
        ('/testbed/sklearn/feature_selection/tests/test_base.py', 'validate_data not available'),
        ('/testbed/sklearn/feature_selection/tests/test_rfe.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/inspection/tests/test_partial_dependence.py', '_build_pruned_tree_py not available'),
        ('/testbed/sklearn/linear_model/tests/test_ridge.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/metrics/tests/test_pairwise_distances_reduction.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/model_selection/tests/test_successive_halving.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/model_selection/tests/test_validation.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/neighbors/tests/test_nca.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/neighbors/tests/test_neighbors.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/preprocessing/tests/test_data.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/semi_supervised/tests/test_self_training.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/tests/test_pipeline.py', '_check_feature_names not available'),
    ]

    for filepath, reason in files_to_skip:
        skip_entire_file(filepath, reason)


def patch_test_sgd():
    """Patch test_sgd.py to handle missing get_tags."""
    filepath = '/testbed/sklearn/linear_model/tests/test_sgd.py'

    patches = [
        # Comment out the get_tags import
        (
            'from sklearn.utils import get_tags',
            '# [ENV-PATCH] from sklearn.utils import get_tags  # not available in M13'
        ),
    ]

    if patch_file(filepath, patches):
        # Also add skip decorator to the test that uses get_tags
        with open(filepath, 'r') as f:
            content = f.read()

        # Add skip decorator to test_sgdocsvm_attributes_and_estimator_type if not already skipped
        if 'def test_sgdocsvm_attributes_and_estimator_type' in content:
            if '@pytest.mark.skip' not in content.split('def test_sgdocsvm_attributes_and_estimator_type')[0][-200:]:
                content = re.sub(
                    r'(def test_sgdocsvm_attributes_and_estimator_type\()',
                    r'@pytest.mark.skip(reason="[ENV-PATCH] get_tags not available in M13")\n\1',
                    content
                )
                with open(filepath, 'w') as f:
                    f.write(content)
                print(f"Added skip decorator to test_sgdocsvm_attributes_and_estimator_type in {filepath}")


def main():
    print("="*60)
    print("Applying M13 END state patches...")
    print("="*60)

    # Patch test_docstring_parameters.py for END state
    print("\n[CRITICAL] Patching test_docstring_parameters.py for END state...")
    patch_test_docstring_parameters_end()

    # Skip files that don't contain target tests
    print("\n[INFO] Skipping files without target tests (commenting out contents)...")
    patch_files_without_target_tests()

    # Patch test_sgd.py
    print("\n[INFO] Patching test_sgd.py...")
    patch_test_sgd()

    print("\n" + "="*60)
    print("M13 END state patches complete!")
    print("="*60)


if __name__ == '__main__':
    main()
EOFPATCH

# Apply the M13 patches for END state
RUN cd /testbed && python /tmp/patch_m13_end.py

# Commit patches and update END tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Apply M13 test patches for END state" && \
    git tag -f milestone-M13-end HEAD

# Create a separate patch script for START state
RUN cat > /tmp/patch_m13_start.py << 'EOFPATCH'
#!/usr/bin/env python3
"""
Patch test files for M13 START state compatibility.
In START state, assert_docstring_consistency and skip_if_no_numpydoc don't exist yet
in sklearn.utils._testing.
"""
import re
import os

def patch_file(filepath, patches):
    """Apply string replacement patches to a file."""
    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return False

    with open(filepath, 'r') as f:
        content = f.read()

    original = content
    for old, new in patches:
        content = content.replace(old, new)

    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Patched: {filepath}")
        return True
    else:
        print(f"No changes needed: {filepath}")
        return False


def skip_entire_file(filepath, reason):
    """
    Skip an entire file by commenting out all its content and adding a skip marker.
    This prevents import errors since the imports won't be executed.
    """
    if not os.path.exists(filepath):
        print(f"File not found (expected): {filepath}")
        return

    with open(filepath, 'r') as f:
        lines = f.readlines()

    # Check if already patched
    if lines and '[ENV-PATCH]' in lines[0]:
        print(f"Already patched: {filepath}")
        return

    with open(filepath, 'w') as f:
        f.write(f'# [ENV-PATCH] {reason}\n')
        f.write('# This file is skipped because it imports modules not available\n')
        f.write('import pytest\n')
        f.write(f'pytestmark = pytest.mark.skip(reason="[ENV-PATCH] {reason}")\n\n')
        # Comment out all existing lines
        for line in lines:
            if line.strip():
                f.write(f"# {line}")
            else:
                f.write(line)

    print(f"Patched (skip entire file): {filepath}")


def patch_test_docstring_parameters_start():
    """
    Patch test_docstring_parameters.py for START state.
    Wrap both _construct_instances and assert_docstring_consistency/skip_if_no_numpydoc in try-except.
    """
    filepath = '/testbed/sklearn/tests/test_docstring_parameters.py'

    patches = [
        # Replace _construct_instances import
        (
            'from sklearn.utils._test_common.instance_generator import _construct_instances',
            '''try:
    from sklearn.utils._test_common.instance_generator import _construct_instances
except ImportError:
    # _test_common module not available in M13
    def _construct_instances(Estimator):
        raise ImportError("_construct_instances not available in M13")'''
        ),
        # Replace _testing imports to handle missing assert_docstring_consistency and skip_if_no_numpydoc
        (
            '''from sklearn.utils._testing import (
    _get_func_name,
    assert_docstring_consistency,
    check_docstring_parameters,
    ignore_warnings,
    skip_if_no_numpydoc,
)''',
            '''from sklearn.utils._testing import (
    _get_func_name,
    check_docstring_parameters,
    ignore_warnings,
)
# [ENV-PATCH] assert_docstring_consistency and skip_if_no_numpydoc don't exist at START state
try:
    from sklearn.utils._testing import assert_docstring_consistency, skip_if_no_numpydoc
except ImportError:
    # These are added by milestone commits - not available at START state
    def assert_docstring_consistency(*args, **kwargs):
        raise ImportError("assert_docstring_consistency not available at START state")
    skip_if_no_numpydoc = lambda f: f  # dummy decorator'''
        ),
    ]

    patch_file(filepath, patches)


def patch_test_testing_start():
    """
    Patch test_testing.py for START state.
    The file imports assert_docstring_consistency and skip_if_no_numpydoc which don't exist at START.
    """
    filepath = '/testbed/sklearn/utils/tests/test_testing.py'

    if not os.path.exists(filepath):
        print(f"File not found: {filepath}")
        return

    # Read the file
    with open(filepath, 'r') as f:
        content = f.read()

    # Check if already patched
    if '[ENV-PATCH]' in content:
        print(f"Already patched: {filepath}")
        return

    # Replace the imports - need to handle the specific import block structure
    # The import is:
    # from sklearn.utils._testing import (
    #     ...
    #     assert_docstring_consistency,
    #     ...
    #     skip_if_no_numpydoc,
    #     ...
    # )

    # Pattern to match the entire import block
    old_import = '''from sklearn.utils._testing import (
    TempMemmap,
    _convert_container,
    _delete_folder,
    _get_warnings_filters_info_list,
    assert_allclose,
    assert_allclose_dense_sparse,
    assert_docstring_consistency,
    assert_run_python_script_without_output,
    check_docstring_parameters,
    create_memmap_backed_data,
    ignore_warnings,
    raises,
    set_random_state,
    skip_if_no_numpydoc,
    turn_warnings_into_errors,
)'''

    new_import = '''from sklearn.utils._testing import (
    TempMemmap,
    _convert_container,
    _delete_folder,
    _get_warnings_filters_info_list,
    assert_allclose,
    assert_allclose_dense_sparse,
    assert_run_python_script_without_output,
    check_docstring_parameters,
    create_memmap_backed_data,
    ignore_warnings,
    raises,
    set_random_state,
    turn_warnings_into_errors,
)
# [ENV-PATCH] assert_docstring_consistency and skip_if_no_numpydoc don't exist at START state
try:
    from sklearn.utils._testing import assert_docstring_consistency, skip_if_no_numpydoc
except ImportError:
    # These are added by milestone commits - not available at START state
    def assert_docstring_consistency(*args, **kwargs):
        raise ImportError("assert_docstring_consistency not available at START state")
    skip_if_no_numpydoc = lambda f: f  # dummy decorator'''

    if old_import in content:
        content = content.replace(old_import, new_import)
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"Patched: {filepath}")
    else:
        print(f"Could not find expected import block in: {filepath}")


def patch_files_without_target_tests():
    """Skip files that have import errors but don't contain target tests."""

    files_to_skip = [
        ('/testbed/sklearn/frozen/tests/test_frozen.py', 'FrozenEstimator/is_clusterer not available in M13'),
        ('/testbed/sklearn/tests/test_metaestimators.py', '_test_common module not available in M13'),
        ('/testbed/sklearn/utils/tests/test_plotting.py', '_despine not available in M13'),
        ('/testbed/sklearn/tests/test_calibration.py', 'FrozenEstimator not available in M13'),
        ('/testbed/sklearn/tests/test_base.py', 'Multiple APIs not available in M13'),
        ('/testbed/sklearn/utils/tests/test_tags.py', 'get_tags function not available in M13'),
        ('/testbed/sklearn/utils/tests/test_unique.py', 'sklearn.utils._unique module not available in M13'),
        ('/testbed/sklearn/utils/tests/test_cython_blas.py', 'ColMajor/NoTrans not available in M13'),
        ('/testbed/sklearn/utils/tests/test_array_api.py', '_count_nonzero not available in M13'),
        ('/testbed/sklearn/utils/tests/test_estimator_checks.py', 'get_tags/default_tags not available in M13'),
        ('/testbed/sklearn/utils/tests/test_validation.py', 'get_tags not available in M13'),
        ('/testbed/sklearn/tests/test_common.py', 'get_tags not available in M13'),
        ('/testbed/sklearn/tree/tests/test_tree.py', '_build_pruned_tree_py not available in M13'),
        ('/testbed/sklearn/model_selection/tests/test_search.py', '_yield_masked_array_for_each_param not available in M13'),
        ('/testbed/sklearn/metrics/tests/test_dist_metrics.py', 'DEPRECATED_METRICS not available'),
        ('/testbed/sklearn/cluster/tests/test_hierarchical.py', 'DEPRECATED_METRICS from test_dist_metrics'),
        ('/testbed/sklearn/cluster/tests/test_k_means.py', '_get_threadpool_controller/_test_common not available'),
        ('/testbed/sklearn/decomposition/tests/test_pca.py', '_test_common not available'),
        ('/testbed/sklearn/feature_selection/tests/test_base.py', 'validate_data not available'),
        ('/testbed/sklearn/feature_selection/tests/test_rfe.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/inspection/tests/test_partial_dependence.py', '_build_pruned_tree_py not available'),
        ('/testbed/sklearn/linear_model/tests/test_ridge.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/metrics/tests/test_pairwise_distances_reduction.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/model_selection/tests/test_successive_halving.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/model_selection/tests/test_validation.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/neighbors/tests/test_nca.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/neighbors/tests/test_neighbors.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/preprocessing/tests/test_data.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/semi_supervised/tests/test_self_training.py', 'get_tags/_test_common not available'),
        ('/testbed/sklearn/tests/test_pipeline.py', '_check_feature_names not available'),
    ]

    for filepath, reason in files_to_skip:
        skip_entire_file(filepath, reason)


def patch_test_sgd():
    """Patch test_sgd.py to handle missing get_tags."""
    filepath = '/testbed/sklearn/linear_model/tests/test_sgd.py'

    patches = [
        # Comment out the get_tags import
        (
            'from sklearn.utils import get_tags',
            '# [ENV-PATCH] from sklearn.utils import get_tags  # not available in M13'
        ),
    ]

    if patch_file(filepath, patches):
        # Also add skip decorator to the test that uses get_tags
        with open(filepath, 'r') as f:
            content = f.read()

        # Add skip decorator to test_sgdocsvm_attributes_and_estimator_type if not already skipped
        if 'def test_sgdocsvm_attributes_and_estimator_type' in content:
            if '@pytest.mark.skip' not in content.split('def test_sgdocsvm_attributes_and_estimator_type')[0][-200:]:
                content = re.sub(
                    r'(def test_sgdocsvm_attributes_and_estimator_type\()',
                    r'@pytest.mark.skip(reason="[ENV-PATCH] get_tags not available in M13")\n\1',
                    content
                )
                with open(filepath, 'w') as f:
                    f.write(content)
                print(f"Added skip decorator to test_sgdocsvm_attributes_and_estimator_type in {filepath}")


def main():
    print("="*60)
    print("Applying M13 START state patches...")
    print("="*60)

    # Patch test_docstring_parameters.py for START state
    print("\n[CRITICAL] Patching test_docstring_parameters.py for START state...")
    patch_test_docstring_parameters_start()

    # Patch test_testing.py for START state
    print("\n[CRITICAL] Patching test_testing.py for START state...")
    patch_test_testing_start()

    # Skip files that don't contain target tests
    print("\n[INFO] Skipping files without target tests (commenting out contents)...")
    patch_files_without_target_tests()

    # Patch test_sgd.py
    print("\n[INFO] Patching test_sgd.py...")
    patch_test_sgd()

    print("\n" + "="*60)
    print("M13 START state patches complete!")
    print("="*60)


if __name__ == '__main__':
    main()
EOFPATCH

# Now apply patches to START state
RUN cd /testbed && git checkout milestone-M13-start

# Apply patches to START state
RUN cd /testbed && python /tmp/patch_m13_start.py

# Commit patches and update START tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Apply M13 test patches for START state" && \
    git tag -f milestone-M13-start HEAD

# Rebuild scikit-learn for START state (default state)
RUN cd /testbed && pip install --no-build-isolation --editable .
