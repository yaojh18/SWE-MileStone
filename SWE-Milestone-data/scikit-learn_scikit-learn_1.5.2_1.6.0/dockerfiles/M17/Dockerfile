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

# Set git safe directory and configure git user for commits
RUN git config --global --add safe.directory /testbed && \
    git config --global user.email "ci@test.com" && \
    git config --global user.name "CI"

# [ENV-PATCH] Add missing sklearn.frozen module required by sklearn/calibration.py
# The FrozenEstimator was added in commit 4ee3afa5524 but wasn't included in the M17 milestone branch
# Note: Uses _safe_tags instead of get_tags since the latter doesn't exist in this version
RUN mkdir -p /testbed/sklearn/frozen/tests && \
    cat > /testbed/sklearn/frozen/__init__.py << 'EOF'
# Authors: The scikit-learn developers
# SPDX-License-Identifier: BSD-3-Clause

from ._frozen import FrozenEstimator

__all__ = ["FrozenEstimator"]
EOF
RUN cat > /testbed/sklearn/frozen/_frozen.py << 'FROZENEOF'
# Authors: The scikit-learn developers
# SPDX-License-Identifier: BSD-3-Clause

from copy import deepcopy

from ..base import BaseEstimator
from ..exceptions import NotFittedError
from ..utils._tags import _safe_tags, _DEFAULT_TAGS
from ..utils.metaestimators import available_if
from ..utils.validation import check_is_fitted


def _estimator_has(attr):
    """Check that final_estimator has `attr`.

    Used together with `available_if`.
    """

    def check(self):
        # raise original `AttributeError` if `attr` does not exist
        getattr(self.estimator, attr)
        return True

    return check


class FrozenEstimator(BaseEstimator):
    """Estimator that wraps a fitted estimator to prevent re-fitting.

    This meta-estimator takes an estimator and freezes it, in the sense that calling
    `fit` on it has no effect. `fit_predict` and `fit_transform` are also disabled.
    All other methods are delegated to the original estimator and original estimator's
    attributes are accessible as well.

    This is particularly useful when you have a fitted or a pre-trained model as a
    transformer in a pipeline, and you'd like `pipeline.fit` to have no effect on this
    step.

    Parameters
    ----------
    estimator : estimator
        The estimator which is to be kept frozen.

    See Also
    --------
    None: No similar entry in the scikit-learn documentation.

    Examples
    --------
    >>> from sklearn.datasets import make_classification
    >>> from sklearn.frozen import FrozenEstimator
    >>> from sklearn.linear_model import LogisticRegression
    >>> X, y = make_classification(random_state=0)
    >>> clf = LogisticRegression(random_state=0).fit(X, y)
    >>> frozen_clf = FrozenEstimator(clf)
    >>> frozen_clf.fit(X, y)  # No-op
    FrozenEstimator(estimator=LogisticRegression(random_state=0))
    >>> frozen_clf.predict(X)  # Predictions from `clf.predict`
    array(...)
    """

    def __init__(self, estimator):
        self.estimator = estimator

    @available_if(_estimator_has("__getitem__"))
    def __getitem__(self, *args, **kwargs):
        """__getitem__ is defined in :class:`~sklearn.pipeline.Pipeline` and \
            :class:`~sklearn.compose.ColumnTransformer`.
        """
        return self.estimator.__getitem__(*args, **kwargs)

    def __getattr__(self, name):
        # `estimator`'s attributes are now accessible except `fit_predict` and
        # `fit_transform`
        if name in ["fit_predict", "fit_transform"]:
            raise AttributeError(f"{name} is not available for frozen estimators.")
        return getattr(self.estimator, name)

    def __sklearn_clone__(self):
        return self

    def __sklearn_is_fitted__(self):
        try:
            check_is_fitted(self.estimator)
            return True
        except NotFittedError:
            return False

    def fit(self, X, y, *args, **kwargs):
        """No-op.

        As a frozen estimator, calling `fit` has no effect.

        Parameters
        ----------
        X : object
            Ignored.

        y : object
            Ignored.

        *args : tuple
            Additional positional arguments. Ignored, but present for API compatibility
            with `self.estimator`.

        **kwargs : dict
            Additional keyword arguments. Ignored, but present for API compatibility
            with `self.estimator`.

        Returns
        -------
        self : object
            Returns the instance itself.
        """
        check_is_fitted(self.estimator)
        return self

    def set_params(self, **kwargs):
        """Set the parameters of this estimator.

        The only valid key here is `estimator`. You cannot set the parameters of the
        inner estimator.

        Parameters
        ----------
        **kwargs : dict
            Estimator parameters.

        Returns
        -------
        self : FrozenEstimator
            This estimator.
        """
        estimator = kwargs.pop("estimator", None)
        if estimator is not None:
            self.estimator = estimator
        if kwargs:
            raise ValueError(
                "You cannot set parameters of the inner estimator in a frozen "
                "estimator since calling `fit` has no effect. You can use "
                "`frozenestimator.estimator.set_params` to set parameters of the inner "
                "estimator."
            )

    def get_params(self, deep=True):
        """Get parameters for this estimator.

        Returns a `{"estimator": estimator}` dict. The parameters of the inner
        estimator are not included.

        Parameters
        ----------
        deep : bool, default=True
            Ignored.

        Returns
        -------
        params : dict
            Parameter names mapped to their values.
        """
        return {"estimator": self.estimator}

    def _more_tags(self):
        # Return tags dict with _skip_test = True
        tags = deepcopy(_safe_tags(self.estimator))
        tags["_skip_test"] = True
        return tags
FROZENEOF
RUN touch /testbed/sklearn/frozen/tests/__init__.py

# Checkout to END state (all features available)
# Tags already exist in testbed - just checkout, don't create tags
RUN cd /testbed && git checkout milestone-M17-end

# Commit the frozen module patch and move the tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing sklearn.frozen module for test collection" && \
    git tag -f milestone-M17-end HEAD

# Rebuild scikit-learn for END state
RUN cd /testbed && pip install --no-build-isolation --editable .

# Set default git state to START
RUN cd /testbed && git checkout milestone-M17-start

# Add frozen module to START state as well
RUN mkdir -p /testbed/sklearn/frozen/tests && \
    cat > /testbed/sklearn/frozen/__init__.py << 'EOF'
# Authors: The scikit-learn developers
# SPDX-License-Identifier: BSD-3-Clause

from ._frozen import FrozenEstimator

__all__ = ["FrozenEstimator"]
EOF
RUN cat > /testbed/sklearn/frozen/_frozen.py << 'FROZENEOF'
# Authors: The scikit-learn developers
# SPDX-License-Identifier: BSD-3-Clause

from copy import deepcopy

from ..base import BaseEstimator
from ..exceptions import NotFittedError
from ..utils._tags import _safe_tags, _DEFAULT_TAGS
from ..utils.metaestimators import available_if
from ..utils.validation import check_is_fitted


def _estimator_has(attr):
    """Check that final_estimator has `attr`.

    Used together with `available_if`.
    """

    def check(self):
        # raise original `AttributeError` if `attr` does not exist
        getattr(self.estimator, attr)
        return True

    return check


class FrozenEstimator(BaseEstimator):
    """Estimator that wraps a fitted estimator to prevent re-fitting.

    This meta-estimator takes an estimator and freezes it, in the sense that calling
    `fit` on it has no effect. `fit_predict` and `fit_transform` are also disabled.
    All other methods are delegated to the original estimator and original estimator's
    attributes are accessible as well.

    This is particularly useful when you have a fitted or a pre-trained model as a
    transformer in a pipeline, and you'd like `pipeline.fit` to have no effect on this
    step.

    Parameters
    ----------
    estimator : estimator
        The estimator which is to be kept frozen.

    See Also
    --------
    None: No similar entry in the scikit-learn documentation.

    Examples
    --------
    >>> from sklearn.datasets import make_classification
    >>> from sklearn.frozen import FrozenEstimator
    >>> from sklearn.linear_model import LogisticRegression
    >>> X, y = make_classification(random_state=0)
    >>> clf = LogisticRegression(random_state=0).fit(X, y)
    >>> frozen_clf = FrozenEstimator(clf)
    >>> frozen_clf.fit(X, y)  # No-op
    FrozenEstimator(estimator=LogisticRegression(random_state=0))
    >>> frozen_clf.predict(X)  # Predictions from `clf.predict`
    array(...)
    """

    def __init__(self, estimator):
        self.estimator = estimator

    @available_if(_estimator_has("__getitem__"))
    def __getitem__(self, *args, **kwargs):
        """__getitem__ is defined in :class:`~sklearn.pipeline.Pipeline` and \
            :class:`~sklearn.compose.ColumnTransformer`.
        """
        return self.estimator.__getitem__(*args, **kwargs)

    def __getattr__(self, name):
        # `estimator`'s attributes are now accessible except `fit_predict` and
        # `fit_transform`
        if name in ["fit_predict", "fit_transform"]:
            raise AttributeError(f"{name} is not available for frozen estimators.")
        return getattr(self.estimator, name)

    def __sklearn_clone__(self):
        return self

    def __sklearn_is_fitted__(self):
        try:
            check_is_fitted(self.estimator)
            return True
        except NotFittedError:
            return False

    def fit(self, X, y, *args, **kwargs):
        """No-op.

        As a frozen estimator, calling `fit` has no effect.

        Parameters
        ----------
        X : object
            Ignored.

        y : object
            Ignored.

        *args : tuple
            Additional positional arguments. Ignored, but present for API compatibility
            with `self.estimator`.

        **kwargs : dict
            Additional keyword arguments. Ignored, but present for API compatibility
            with `self.estimator`.

        Returns
        -------
        self : object
            Returns the instance itself.
        """
        check_is_fitted(self.estimator)
        return self

    def set_params(self, **kwargs):
        """Set the parameters of this estimator.

        The only valid key here is `estimator`. You cannot set the parameters of the
        inner estimator.

        Parameters
        ----------
        **kwargs : dict
            Estimator parameters.

        Returns
        -------
        self : FrozenEstimator
            This estimator.
        """
        estimator = kwargs.pop("estimator", None)
        if estimator is not None:
            self.estimator = estimator
        if kwargs:
            raise ValueError(
                "You cannot set parameters of the inner estimator in a frozen "
                "estimator since calling `fit` has no effect. You can use "
                "`frozenestimator.estimator.set_params` to set parameters of the inner "
                "estimator."
            )

    def get_params(self, deep=True):
        """Get parameters for this estimator.

        Returns a `{"estimator": estimator}` dict. The parameters of the inner
        estimator are not included.

        Parameters
        ----------
        deep : bool, default=True
            Ignored.

        Returns
        -------
        params : dict
            Parameter names mapped to their values.
        """
        return {"estimator": self.estimator}

    def _more_tags(self):
        # Return tags dict with _skip_test = True
        tags = deepcopy(_safe_tags(self.estimator))
        tags["_skip_test"] = True
        return tags
FROZENEOF
RUN touch /testbed/sklearn/frozen/tests/__init__.py

# Commit the frozen module patch for START state and move the tag
RUN cd /testbed && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add missing sklearn.frozen module for test collection" && \
    git tag -f milestone-M17-start HEAD

# Rebuild scikit-learn for START state (final state)
RUN cd /testbed && pip install --no-build-isolation --editable .
