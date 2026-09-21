# Build on pre-configured base image
FROM scikit-learn_scikit-learn_1.5.2_1.6.0/base:latest

# Set umask to ensure files created in container are world-writable
# This prevents permission issues when test results are written to mounted volumes
RUN echo 'umask 000' >> /etc/bash.bashrc && \
    echo '#!/bin/bash\numask 000\nexec "$@"' > /entrypoint.sh && \
    chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["bash"]

# Configure git user for commits
RUN git config --global user.email "harness@test.local" && \
    git config --global user.name "Test Harness"

# Remove the original /testbed from base image and copy local testbed
# NOTE: The testbed already contains milestone tags - DO NOT create them with `git tag`
RUN rm -rf /testbed
COPY . /testbed/

# [ENV-PATCH] Add Tags dataclasses and default_tags function to _tags.py
# The milestone tags have test files that import default_tags from sklearn.utils._tags,
# but the source code at the milestone state doesn't have this function.
# This patch adds the necessary compatibility shim.
RUN cd /testbed && git checkout milestone-M01-end && \
    echo 'from __future__ import annotations' > /tmp/tags_patch.py && \
    echo 'from dataclasses import dataclass, field' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class InputTags:' >> /tmp/tags_patch.py && \
    echo '    one_d_array: bool = False' >> /tmp/tags_patch.py && \
    echo '    two_d_array: bool = True' >> /tmp/tags_patch.py && \
    echo '    three_d_array: bool = False' >> /tmp/tags_patch.py && \
    echo '    sparse: bool = False' >> /tmp/tags_patch.py && \
    echo '    categorical: bool = False' >> /tmp/tags_patch.py && \
    echo '    string: bool = False' >> /tmp/tags_patch.py && \
    echo '    dict: bool = False' >> /tmp/tags_patch.py && \
    echo '    positive_only: bool = False' >> /tmp/tags_patch.py && \
    echo '    allow_nan: bool = False' >> /tmp/tags_patch.py && \
    echo '    pairwise: bool = False' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class TargetTags:' >> /tmp/tags_patch.py && \
    echo '    required: bool' >> /tmp/tags_patch.py && \
    echo '    one_d_labels: bool = False' >> /tmp/tags_patch.py && \
    echo '    two_d_labels: bool = False' >> /tmp/tags_patch.py && \
    echo '    positive_only: bool = False' >> /tmp/tags_patch.py && \
    echo '    multi_output: bool = False' >> /tmp/tags_patch.py && \
    echo '    single_output: bool = True' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class TransformerTags:' >> /tmp/tags_patch.py && \
    echo '    preserves_dtype: list[str] = field(default_factory=lambda: ["float64"])' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class ClassifierTags:' >> /tmp/tags_patch.py && \
    echo '    poor_score: bool = False' >> /tmp/tags_patch.py && \
    echo '    multi_class: bool = True' >> /tmp/tags_patch.py && \
    echo '    multi_label: bool = False' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class RegressorTags:' >> /tmp/tags_patch.py && \
    echo '    poor_score: bool = False' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class Tags:' >> /tmp/tags_patch.py && \
    echo '    estimator_type: str | None' >> /tmp/tags_patch.py && \
    echo '    target_tags: TargetTags' >> /tmp/tags_patch.py && \
    echo '    transformer_tags: TransformerTags | None = None' >> /tmp/tags_patch.py && \
    echo '    classifier_tags: ClassifierTags | None = None' >> /tmp/tags_patch.py && \
    echo '    regressor_tags: RegressorTags | None = None' >> /tmp/tags_patch.py && \
    echo '    array_api_support: bool = False' >> /tmp/tags_patch.py && \
    echo '    no_validation: bool = False' >> /tmp/tags_patch.py && \
    echo '    non_deterministic: bool = False' >> /tmp/tags_patch.py && \
    echo '    requires_fit: bool = True' >> /tmp/tags_patch.py && \
    echo '    _skip_test: bool = False' >> /tmp/tags_patch.py && \
    echo '    input_tags: InputTags = field(default_factory=InputTags)' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo 'def default_tags(estimator) -> Tags:' >> /tmp/tags_patch.py && \
    echo '    est_is_classifier = getattr(estimator, "_estimator_type", None) == "classifier"' >> /tmp/tags_patch.py && \
    echo '    est_is_regressor = getattr(estimator, "_estimator_type", None) == "regressor"' >> /tmp/tags_patch.py && \
    echo '    target_required = est_is_classifier or est_is_regressor' >> /tmp/tags_patch.py && \
    echo '    return Tags(' >> /tmp/tags_patch.py && \
    echo '        estimator_type=getattr(estimator, "_estimator_type", None),' >> /tmp/tags_patch.py && \
    echo '        target_tags=TargetTags(required=target_required),' >> /tmp/tags_patch.py && \
    echo '        transformer_tags=(' >> /tmp/tags_patch.py && \
    echo '            TransformerTags()' >> /tmp/tags_patch.py && \
    echo '            if hasattr(estimator, "transform") or hasattr(estimator, "fit_transform")' >> /tmp/tags_patch.py && \
    echo '            else None' >> /tmp/tags_patch.py && \
    echo '        ),' >> /tmp/tags_patch.py && \
    echo '        classifier_tags=ClassifierTags() if est_is_classifier else None,' >> /tmp/tags_patch.py && \
    echo '        regressor_tags=RegressorTags() if est_is_regressor else None,' >> /tmp/tags_patch.py && \
    echo '    )' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    cat /tmp/tags_patch.py sklearn/utils/_tags.py > /tmp/_tags_new.py && \
    mv /tmp/_tags_new.py sklearn/utils/_tags.py && \
    rm /tmp/tags_patch.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add Tags dataclasses and default_tags function for compatibility" && \
    git tag -f milestone-M01-end HEAD

# Apply same patch to START state
RUN cd /testbed && git checkout milestone-M01-start && \
    echo 'from __future__ import annotations' > /tmp/tags_patch.py && \
    echo 'from dataclasses import dataclass, field' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class InputTags:' >> /tmp/tags_patch.py && \
    echo '    one_d_array: bool = False' >> /tmp/tags_patch.py && \
    echo '    two_d_array: bool = True' >> /tmp/tags_patch.py && \
    echo '    three_d_array: bool = False' >> /tmp/tags_patch.py && \
    echo '    sparse: bool = False' >> /tmp/tags_patch.py && \
    echo '    categorical: bool = False' >> /tmp/tags_patch.py && \
    echo '    string: bool = False' >> /tmp/tags_patch.py && \
    echo '    dict: bool = False' >> /tmp/tags_patch.py && \
    echo '    positive_only: bool = False' >> /tmp/tags_patch.py && \
    echo '    allow_nan: bool = False' >> /tmp/tags_patch.py && \
    echo '    pairwise: bool = False' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class TargetTags:' >> /tmp/tags_patch.py && \
    echo '    required: bool' >> /tmp/tags_patch.py && \
    echo '    one_d_labels: bool = False' >> /tmp/tags_patch.py && \
    echo '    two_d_labels: bool = False' >> /tmp/tags_patch.py && \
    echo '    positive_only: bool = False' >> /tmp/tags_patch.py && \
    echo '    multi_output: bool = False' >> /tmp/tags_patch.py && \
    echo '    single_output: bool = True' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class TransformerTags:' >> /tmp/tags_patch.py && \
    echo '    preserves_dtype: list[str] = field(default_factory=lambda: ["float64"])' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class ClassifierTags:' >> /tmp/tags_patch.py && \
    echo '    poor_score: bool = False' >> /tmp/tags_patch.py && \
    echo '    multi_class: bool = True' >> /tmp/tags_patch.py && \
    echo '    multi_label: bool = False' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class RegressorTags:' >> /tmp/tags_patch.py && \
    echo '    poor_score: bool = False' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo '@dataclass(slots=True)' >> /tmp/tags_patch.py && \
    echo 'class Tags:' >> /tmp/tags_patch.py && \
    echo '    estimator_type: str | None' >> /tmp/tags_patch.py && \
    echo '    target_tags: TargetTags' >> /tmp/tags_patch.py && \
    echo '    transformer_tags: TransformerTags | None = None' >> /tmp/tags_patch.py && \
    echo '    classifier_tags: ClassifierTags | None = None' >> /tmp/tags_patch.py && \
    echo '    regressor_tags: RegressorTags | None = None' >> /tmp/tags_patch.py && \
    echo '    array_api_support: bool = False' >> /tmp/tags_patch.py && \
    echo '    no_validation: bool = False' >> /tmp/tags_patch.py && \
    echo '    non_deterministic: bool = False' >> /tmp/tags_patch.py && \
    echo '    requires_fit: bool = True' >> /tmp/tags_patch.py && \
    echo '    _skip_test: bool = False' >> /tmp/tags_patch.py && \
    echo '    input_tags: InputTags = field(default_factory=InputTags)' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    echo 'def default_tags(estimator) -> Tags:' >> /tmp/tags_patch.py && \
    echo '    est_is_classifier = getattr(estimator, "_estimator_type", None) == "classifier"' >> /tmp/tags_patch.py && \
    echo '    est_is_regressor = getattr(estimator, "_estimator_type", None) == "regressor"' >> /tmp/tags_patch.py && \
    echo '    target_required = est_is_classifier or est_is_regressor' >> /tmp/tags_patch.py && \
    echo '    return Tags(' >> /tmp/tags_patch.py && \
    echo '        estimator_type=getattr(estimator, "_estimator_type", None),' >> /tmp/tags_patch.py && \
    echo '        target_tags=TargetTags(required=target_required),' >> /tmp/tags_patch.py && \
    echo '        transformer_tags=(' >> /tmp/tags_patch.py && \
    echo '            TransformerTags()' >> /tmp/tags_patch.py && \
    echo '            if hasattr(estimator, "transform") or hasattr(estimator, "fit_transform")' >> /tmp/tags_patch.py && \
    echo '            else None' >> /tmp/tags_patch.py && \
    echo '        ),' >> /tmp/tags_patch.py && \
    echo '        classifier_tags=ClassifierTags() if est_is_classifier else None,' >> /tmp/tags_patch.py && \
    echo '        regressor_tags=RegressorTags() if est_is_regressor else None,' >> /tmp/tags_patch.py && \
    echo '    )' >> /tmp/tags_patch.py && \
    echo '' >> /tmp/tags_patch.py && \
    cat /tmp/tags_patch.py sklearn/utils/_tags.py > /tmp/_tags_new.py && \
    mv /tmp/_tags_new.py sklearn/utils/_tags.py && \
    rm /tmp/tags_patch.py && \
    git add -A && \
    git commit -m "[ENV-PATCH] Add Tags dataclasses and default_tags function for compatibility" && \
    git tag -f milestone-M01-start HEAD

# Rebuild scikit-learn at END state to include changes
RUN cd /testbed && git checkout milestone-M01-end && \
    pip install --no-build-isolation --editable .

# Set default git state to START
RUN cd /testbed && git checkout milestone-M01-start
