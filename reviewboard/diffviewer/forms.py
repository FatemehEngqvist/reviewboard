"""Forms for uploading diffs."""

from __future__ import unicode_literals

from dateutil.parser import isoparse
from django import forms
from django.core.exceptions import ValidationError
from django.utils.encoding import smart_unicode
from django.utils.translation import ugettext, ugettext_lazy as _

from reviewboard.diffviewer.models import DiffCommit, DiffSet
from reviewboard.diffviewer.validators import (COMMIT_ID_LENGTH,
                                               validate_commit_id)


class UploadCommitForm(forms.Form):
    """The form for uploading a diff and creating a DiffCommit."""

    diff = forms.FileField(
        label=_('Diff'),
        help_text=_('The new diff to upload.'))

    parent_diff = forms.FileField(
        label=_('Parent diff'),
        help_text=_('An optional diff that the main diff is based on. '
                    'This is usually used for distributed revision control '
                    'systems (Git, Mercurial, etc.).'),
        required=False)

    commit_id = forms.CharField(
        label=_('Commit ID'),
        help_text=_('The ID of this commit.'),
        max_length=COMMIT_ID_LENGTH,
        validators=[validate_commit_id])

    parent_id = forms.CharField(
        label=_('Parent commit ID'),
        help_text=_('The ID of the parent commit.'),
        max_length=COMMIT_ID_LENGTH,
        validators=[validate_commit_id])

    commit_message = forms.CharField(
        label=_('Description'),
        help_text=_('The commit message.'))

    author_name = forms.CharField(
        label=_('Author name'),
        help_text=_('The name of the author of this commit.'),
        max_length=DiffCommit.NAME_MAX_LENGTH)

    author_email = forms.CharField(
        label=_('Author e-mail address'),
        help_text=_('The e-mail address of the author of this commit.'),
        max_length=DiffCommit.EMAIL_MAX_LENGTH,
        widget=forms.EmailInput)

    author_date = forms.CharField(
        label=_('Author date'),
        help_text=_('The date and time this commit was authored.'))

    committer_name = forms.CharField(
        label=_('Committer name'),
        help_text=_('The name of the committer of this commit.'),
        max_length=DiffCommit.NAME_MAX_LENGTH,
        required=False)

    committer_email = forms.CharField(
        label=_('Committer e-mail address'),
        help_text=_('The e-mail address of the committer of this commit.'),
        max_length=DiffCommit.EMAIL_MAX_LENGTH,
        widget=forms.EmailInput,
        required=False)

    committer_date = forms.CharField(
        label=_('Committer date'),
        help_text=_('The date and time this commit was committed.'),
        required=False)

    def __init__(self, diffset, request=None, *args, **kwargs):
        """Initialize the form.

        Args:
            diffset (reviewboard.diffviewer.models.diffset.DiffSet):
                The DiffSet to attach the created DiffCommit to.

            request (django.http.HttpRequest, optional):
                The HTTP request from the client.

            *args (tuple):
                Additional positional arguments.

            **kwargs (dict):
                Additional keyword arguments.
        """
        super(UploadCommitForm, self).__init__(*args, **kwargs)

        self.diffset = diffset
        self.request = request

    def create(self):
        """Create the DiffCommit.

        Returns:
            reviewboard.diffviewer.models.diffcommit.DiffCommit:
            The created DiffCommit.
        """
        assert self.is_valid()

        return DiffCommit.objects.create_from_upload(
            request=self.request,
            diffset=self.diffset,
            repository=self.diffset.repository,
            diff_file=self.cleaned_data['diff'],
            parent_diff_file=self.cleaned_data.get('parent_diff'),
            commit_message=self.cleaned_data['commit_message'],
            commit_id=self.cleaned_data['commit_id'],
            parent_id=self.cleaned_data['parent_id'],
            author_name=self.cleaned_data['author_name'],
            author_email=self.cleaned_data['author_email'],
            author_date=self.cleaned_data['author_date'],
            committer_name=self.cleaned_data.get('committer_name'),
            committer_email=self.cleaned_data.get('committer_email'),
            committer_date=self.cleaned_data.get('committer_date'))

    def clean(self):
        """Clean the form.

        Returns:
            dict:
            The cleaned form data.

        Raises:
            django.core.exceptions.ValidationError:
                The form data was not valid.
        """
        super(UploadCommitForm, self).clean()

        if self.diffset.history_id is not None:
            # A diffset will have a history attached if and only if it has been
            # published, in which case we cannot attach further commits to it.
            raise ValidationError(ugettext(
                'Cannot upload commits to a published diff.'))

        return self.cleaned_data

    def clean_author_date(self):
        """Parse the date and time in the ``author_date`` field.

        Returns:
            datetime.datetime:
            The parsed date and time.
        """
        try:
            return isoparse(self.cleaned_data['author_date'])
        except ValueError:
            raise ValidationError(ugettext(
                'This date must be in ISO 8601 format.'))

    def clean_committer_date(self):
        """Parse the date and time in the ``committer_date`` field.

        Returns:
            datetime.datetime:
            The parsed date and time.
        """
        try:
            return isoparse(self.cleaned_data['committer_date'])
        except ValueError:
            raise ValidationError(ugettext(
                'This date must be in ISO 8601 format.'))


class UploadDiffForm(forms.Form):
    """The form for uploading a diff and creating a DiffSet."""

    path = forms.FileField(
        label=_('Diff'),
        help_text=_('The new diff to upload.'))

    parent_diff_path = forms.FileField(
        label=_('Parent Diff'),
        help_text=_('An optional diff that the main diff is based on. '
                    'This is usually used for distributed revision control '
                    'systems (Git, Mercurial, etc.).'),
        required=False)

    basedir = forms.CharField(
        label=_('Base Directory'),
        help_text=_('The absolute path in the repository the diff was '
                    'generated in.'))

    base_commit_id = forms.CharField(
        label=_('Base Commit ID'),
        help_text=_('The ID/revision this change is built upon.'),
        required=False)

    def __init__(self, repository, request=None, *args, **kwargs):
        """Initialize the form.

        Args:
            repository (reviewboard.scmtools.models.Repository):
                The repository the diff will be uploaded against.

            request (django.http.HttpRequest, optional):
                The HTTP request from the client.

            *args (tuple):
                Additional positional arguments.

            **kwrgs (dict):
                Additional keyword arguments.
        """
        super(UploadDiffForm, self).__init__(*args, **kwargs)

        self.repository = repository
        self.request = request

        if repository.get_scmtool().diffs_use_absolute_paths:
            # This SCMTool uses absolute paths, so there's no need to ask
            # the user for the base directory.
            del(self.fields['basedir'])

    def clean_base_commit_id(self):
        """Clean the ``base_commit_id`` field.

        Returns:
            unicode:
            The ``base_commit_id`` field stripped of leading and trailing
            whitespace, or ``None`` if that value would be empty.
        """
        return self.cleaned_data['base_commit_id'].strip() or None

    def clean_basedir(self):
        """Clean the ``basedir`` field.

        Returns:
            unicode:
            The basedir field as a unicode string with leading and trailing
            whitespace removed.
        """
        if self.repository.get_scmtool().diffs_use_absolute_paths:
            return ''

        return smart_unicode(self.cleaned_data['basedir'].strip())

    def create(self, diffset_history=None):
        """Create the DiffSet.

        Args:
            diffset_history (reviewboard.diffviewer.models.diffset_history.
                             DiffSetHistory):
                The DiffSet history to attach the created DiffSet to.

        Returns:
            reviewboard.diffviewer.models.diffset.DiffSet:
            The created DiffSet.
        """
        assert self.is_valid()

        return DiffSet.objects.create_from_upload(
            repository=self.repository,
            diffset_history=diffset_history,
            diff_file=self.cleaned_data['path'],
            parent_diff_file=self.cleaned_data.get('parent_diff_path'),
            basedir=self.cleaned_data.get('basedir', ''),
            base_commit_id=self.cleaned_data['base_commit_id'],
            request=self.request)
