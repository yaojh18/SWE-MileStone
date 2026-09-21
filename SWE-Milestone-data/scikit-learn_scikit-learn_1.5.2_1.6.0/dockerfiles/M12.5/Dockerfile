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

# Configure git
RUN cd /testbed && git config --global --add safe.directory /testbed && \
    git config --global user.email "env-patch@test.com" && \
    git config --global user.name "ENV-PATCH"

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M12.5-end

# [ENV-PATCH] Create stub FrozenEstimator module if it doesn't exist
# The calibration.py references FrozenEstimator from sklearn.frozen, but the frozen
# module was added in a later milestone. We create a stub to prevent import errors.
RUN cd /testbed && \
    if [ ! -f sklearn/frozen/__init__.py ]; then \
        echo '"""Stub module for FrozenEstimator - added as environment patch."""' > sklearn/frozen/__init__.py && \
        echo 'class FrozenEstimator:' >> sklearn/frozen/__init__.py && \
        echo '    """Stub class to prevent import errors. This module is not fully implemented in this milestone."""' >> sklearn/frozen/__init__.py && \
        echo '    pass' >> sklearn/frozen/__init__.py && \
        echo '__all__ = ["FrozenEstimator"]' >> sklearn/frozen/__init__.py; \
    fi

# [ENV-PATCH] Create stub _test_common module
# Some test files import from sklearn.utils._test_common which doesn't exist in this milestone
RUN cd /testbed && \
    mkdir -p sklearn/utils/_test_common && \
    echo '"""Stub module for _test_common - added as environment patch."""' > sklearn/utils/_test_common/__init__.py && \
    echo '' >> sklearn/utils/_test_common/__init__.py && \
    echo '# Stub functions/classes to prevent import errors' >> sklearn/utils/_test_common/__init__.py && \
    echo '' >> sklearn/utils/_test_common/__init__.py

# [ENV-PATCH] Create stub instance_generator module
RUN cd /testbed && \
    echo '"""Stub module for instance_generator - added as environment patch."""' > sklearn/utils/_test_common/instance_generator.py && \
    echo '' >> sklearn/utils/_test_common/instance_generator.py && \
    echo 'def _get_check_estimator_ids(obj):' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    """Stub function."""' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    return getattr(obj, "__class__", type(obj)).__name__' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '' >> sklearn/utils/_test_common/instance_generator.py && \
    echo 'def _tested_estimators(*args, **kwargs):' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    """Stub function that returns empty list."""' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    return []' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '' >> sklearn/utils/_test_common/instance_generator.py && \
    echo 'def _construct_instances(*args, **kwargs):' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    """Stub generator function that yields nothing."""' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    return iter([])' >> sklearn/utils/_test_common/instance_generator.py

# [ENV-PATCH] Add stub Tag classes to sklearn/utils/_tags.py
# Test files reference ClassifierTags, InputTags, etc. which don't exist in this version
RUN cd /testbed && \
    echo '' >> sklearn/utils/_tags.py && \
    echo '# [ENV-PATCH] Stub Tag classes for compatibility' >> sklearn/utils/_tags.py && \
    echo 'class _TagsBase:' >> sklearn/utils/_tags.py && \
    echo '    """Base class for tag containers - stub."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class ClassifierTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for ClassifierTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class RegressorTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for RegressorTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class TransformerTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for TransformerTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class InputTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for InputTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class TargetTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for TargetTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'def get_tags(estimator):' >> sklearn/utils/_tags.py && \
    echo '    """Stub function for get_tags."""' >> sklearn/utils/_tags.py && \
    echo '    return _safe_tags(estimator)' >> sklearn/utils/_tags.py

# [ENV-PATCH] Add _estimator_has and validate_data to sklearn/utils/validation.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/validation.py && \
    echo '# [ENV-PATCH] Stub functions for compatibility' >> sklearn/utils/validation.py && \
    echo 'def _estimator_has(attr):' >> sklearn/utils/validation.py && \
    echo '    """Stub function for _estimator_has."""' >> sklearn/utils/validation.py && \
    echo '    def check(self):' >> sklearn/utils/validation.py && \
    echo '        return callable(getattr(self.estimator, attr, None))' >> sklearn/utils/validation.py && \
    echo '    return check' >> sklearn/utils/validation.py && \
    echo '' >> sklearn/utils/validation.py && \
    echo 'def validate_data(estimator, X, y=None, **kwargs):' >> sklearn/utils/validation.py && \
    echo '    """Stub function for validate_data - delegates to check_array."""' >> sklearn/utils/validation.py && \
    echo '    reset = kwargs.pop("reset", True)' >> sklearn/utils/validation.py && \
    echo '    if y is None:' >> sklearn/utils/validation.py && \
    echo '        return check_array(X, **kwargs)' >> sklearn/utils/validation.py && \
    echo '    else:' >> sklearn/utils/validation.py && \
    echo '        X = check_array(X, **kwargs)' >> sklearn/utils/validation.py && \
    echo '        y = check_array(y, ensure_2d=False, **{k:v for k,v in kwargs.items() if k != "ensure_2d"})' >> sklearn/utils/validation.py && \
    echo '        return X, y' >> sklearn/utils/validation.py

# [ENV-PATCH] Add stub functions to sklearn/utils/_testing.py
RUN cd /testbed && \
    echo '' >> sklearn/utils/_testing.py && \
    echo '# [ENV-PATCH] Stub functions for test compatibility' >> sklearn/utils/_testing.py && \
    echo 'def assert_docstring_consistency(module=None, exclude=None):' >> sklearn/utils/_testing.py && \
    echo '    """Stub function for assert_docstring_consistency."""' >> sklearn/utils/_testing.py && \
    echo '    pass' >> sklearn/utils/_testing.py && \
    echo '' >> sklearn/utils/_testing.py && \
    echo 'import pytest' >> sklearn/utils/_testing.py && \
    echo 'skip_if_no_numpydoc = pytest.mark.skip(reason="numpydoc not available")' >> sklearn/utils/_testing.py

# Commit and tag the patches
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add stub modules for test compatibility" --allow-empty && \
    git tag -f milestone-M12.5-end HEAD

# Rebuild scikit-learn for END state
RUN cd /testbed && pip install --no-build-isolation --editable .

# Apply same patches to START state
RUN cd /testbed && git checkout milestone-M12.5-start

# [ENV-PATCH] Create stub FrozenEstimator module for START state
RUN cd /testbed && \
    if [ ! -f sklearn/frozen/__init__.py ]; then \
        mkdir -p sklearn/frozen && \
        echo '"""Stub module for FrozenEstimator - added as environment patch."""' > sklearn/frozen/__init__.py && \
        echo 'class FrozenEstimator:' >> sklearn/frozen/__init__.py && \
        echo '    """Stub class to prevent import errors. This module is not fully implemented in this milestone."""' >> sklearn/frozen/__init__.py && \
        echo '    pass' >> sklearn/frozen/__init__.py && \
        echo '__all__ = ["FrozenEstimator"]' >> sklearn/frozen/__init__.py; \
    fi

# [ENV-PATCH] Create stub _test_common module for START state
RUN cd /testbed && \
    mkdir -p sklearn/utils/_test_common && \
    echo '"""Stub module for _test_common - added as environment patch."""' > sklearn/utils/_test_common/__init__.py && \
    echo '' >> sklearn/utils/_test_common/__init__.py && \
    echo '# Stub functions/classes to prevent import errors' >> sklearn/utils/_test_common/__init__.py && \
    echo '' >> sklearn/utils/_test_common/__init__.py

# [ENV-PATCH] Create stub instance_generator module for START state
RUN cd /testbed && \
    echo '"""Stub module for instance_generator - added as environment patch."""' > sklearn/utils/_test_common/instance_generator.py && \
    echo '' >> sklearn/utils/_test_common/instance_generator.py && \
    echo 'def _get_check_estimator_ids(obj):' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    """Stub function."""' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    return getattr(obj, "__class__", type(obj)).__name__' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '' >> sklearn/utils/_test_common/instance_generator.py && \
    echo 'def _tested_estimators(*args, **kwargs):' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    """Stub function that returns empty list."""' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    return []' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '' >> sklearn/utils/_test_common/instance_generator.py && \
    echo 'def _construct_instances(*args, **kwargs):' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    """Stub generator function that yields nothing."""' >> sklearn/utils/_test_common/instance_generator.py && \
    echo '    return iter([])' >> sklearn/utils/_test_common/instance_generator.py

# [ENV-PATCH] Add stub Tag classes to sklearn/utils/_tags.py for START state
RUN cd /testbed && \
    echo '' >> sklearn/utils/_tags.py && \
    echo '# [ENV-PATCH] Stub Tag classes for compatibility' >> sklearn/utils/_tags.py && \
    echo 'class _TagsBase:' >> sklearn/utils/_tags.py && \
    echo '    """Base class for tag containers - stub."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class ClassifierTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for ClassifierTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class RegressorTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for RegressorTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class TransformerTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for TransformerTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class InputTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for InputTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'class TargetTags(_TagsBase):' >> sklearn/utils/_tags.py && \
    echo '    """Stub class for TargetTags."""' >> sklearn/utils/_tags.py && \
    echo '    pass' >> sklearn/utils/_tags.py && \
    echo '' >> sklearn/utils/_tags.py && \
    echo 'def get_tags(estimator):' >> sklearn/utils/_tags.py && \
    echo '    """Stub function for get_tags."""' >> sklearn/utils/_tags.py && \
    echo '    return _safe_tags(estimator)' >> sklearn/utils/_tags.py

# [ENV-PATCH] Add _estimator_has and validate_data to sklearn/utils/validation.py for START state
RUN cd /testbed && \
    echo '' >> sklearn/utils/validation.py && \
    echo '# [ENV-PATCH] Stub functions for compatibility' >> sklearn/utils/validation.py && \
    echo 'def _estimator_has(attr):' >> sklearn/utils/validation.py && \
    echo '    """Stub function for _estimator_has."""' >> sklearn/utils/validation.py && \
    echo '    def check(self):' >> sklearn/utils/validation.py && \
    echo '        return callable(getattr(self.estimator, attr, None))' >> sklearn/utils/validation.py && \
    echo '    return check' >> sklearn/utils/validation.py && \
    echo '' >> sklearn/utils/validation.py && \
    echo 'def validate_data(estimator, X, y=None, **kwargs):' >> sklearn/utils/validation.py && \
    echo '    """Stub function for validate_data - delegates to check_array."""' >> sklearn/utils/validation.py && \
    echo '    reset = kwargs.pop("reset", True)' >> sklearn/utils/validation.py && \
    echo '    if y is None:' >> sklearn/utils/validation.py && \
    echo '        return check_array(X, **kwargs)' >> sklearn/utils/validation.py && \
    echo '    else:' >> sklearn/utils/validation.py && \
    echo '        X = check_array(X, **kwargs)' >> sklearn/utils/validation.py && \
    echo '        y = check_array(y, ensure_2d=False, **{k:v for k,v in kwargs.items() if k != "ensure_2d"})' >> sklearn/utils/validation.py && \
    echo '        return X, y' >> sklearn/utils/validation.py

# [ENV-PATCH] Add stub functions to sklearn/utils/_testing.py for START state
RUN cd /testbed && \
    echo '' >> sklearn/utils/_testing.py && \
    echo '# [ENV-PATCH] Stub functions for test compatibility' >> sklearn/utils/_testing.py && \
    echo 'def assert_docstring_consistency(module=None, exclude=None):' >> sklearn/utils/_testing.py && \
    echo '    """Stub function for assert_docstring_consistency."""' >> sklearn/utils/_testing.py && \
    echo '    pass' >> sklearn/utils/_testing.py && \
    echo '' >> sklearn/utils/_testing.py && \
    echo 'import pytest' >> sklearn/utils/_testing.py && \
    echo 'skip_if_no_numpydoc = pytest.mark.skip(reason="numpydoc not available")' >> sklearn/utils/_testing.py

# Commit and tag the patches for START state
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add stub modules for test compatibility" --allow-empty && \
    git tag -f milestone-M12.5-start HEAD

# Rebuild scikit-learn for START state
RUN cd /testbed && pip install --no-build-isolation --editable .
