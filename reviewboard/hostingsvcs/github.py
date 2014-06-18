import re
from django.core.exceptions import ObjectDoesNotExist
from reviewboard.hostingsvcs.repository import RemoteRepository
from reviewboard.hostingsvcs.service import (HostingService,
                                             HostingServiceClient)
from reviewboard.hostingsvcs.utils.paginator import (APIPaginator,
                                                     ProxyPaginator)
class GitHubAPIPaginator(APIPaginator):
    """Paginates over GitHub API list resources.

    This is returned by some GitHubClient functions in order to handle
    iteration over pages of results, without resorting to fetching all
    pages at once or baking pagination into the functions themselves.
    """
    start_query_param = 'page'
    per_page_query_param = 'per_page'

    LINK_RE = re.compile(r'\<(?P<url>[^>]+)\>; rel="(?P<rel>[^"]+)",? *')

    def fetch_url(self, url):
        """Fetches the page data from a URL."""
        data, headers = self.client.api_get(url, return_headers=True)

        # Find all the links in the Link header and key off by the link
        # name ('prev', 'next', etc.).
        links = dict(
            (m.group('rel'), m.group('url'))
            for m in self.LINK_RE.finditer(headers.get('Link', ''))
        )

        return {
            'data': data,
            'headers': headers,
            'prev_url': links.get('prev'),
            'next_url': links.get('next'),
        }


class GitHubClient(HostingServiceClient):
    RAW_MIMETYPE = 'application/vnd.github.v3.raw'

    def __init__(self, hosting_service):
        super(GitHubClient, self).__init__(hosting_service)
        self.account = hosting_service.account

    #
    # HTTP method overrides
    #

    def http_delete(self, url, *args, **kwargs):
        data, headers = super(GitHubClient, self).http_delete(
            url, *args, **kwargs)
        self._check_rate_limits(headers)
        return data, headers

    def http_get(self, url, *args, **kwargs):
        data, headers = super(GitHubClient, self).http_get(
            url, *args, **kwargs)
        self._check_rate_limits(headers)
        return data, headers

    def http_post(self, url, *args, **kwargs):
        data, headers = super(GitHubClient, self).http_post(
            url, *args, **kwargs)
        self._check_rate_limits(headers)
        return data, headers

    #
    # API wrappers around HTTP/JSON methods
    #

    def api_delete(self, url, *args, **kwargs):
        try:
            data, headers = self.json_delete(url, *args, **kwargs)
            return data
        except (URLError, HTTPError) as e:
            self._check_api_error(e)

    def api_get(self, url, return_headers=False, *args, **kwargs):
        """Performs an HTTP GET to the GitHub API and returns the results.

        If `return_headers` is True, then the result of each call (or
        each generated set of data, if using pagination) will be a tuple
        of (data, headers). Otherwise, the result will just be the data.
        """
        try:
            data, headers = self.json_get(url, *args, **kwargs)

            if return_headers:
                return data, headers
            else:
                return data
        except (URLError, HTTPError) as e:
            self._check_api_error(e)

    def api_get_list(self, url, start=None, per_page=None, *args, **kwargs):
        """Performs an HTTP GET to a GitHub API and returns a paginator.

        This returns a GitHubAPIPaginator that's used to iterate over the
        pages of results. Each page contains information on the data and
        headers from that given page.

        The ``start`` and ``per_page`` parameters can be used to control
        where pagination begins and how many results are returned per page.
        ``start`` is a 0-based index representing a page number.
        """
        if start is not None:
            # GitHub uses 1-based indexing, so add one.
            start += 1

        return GitHubAPIPaginator(self, url, start=start, per_page=per_page)

    def api_post(self, url, *args, **kwargs):
        try:
            data, headers = self.json_post(url, *args, **kwargs)
            return data
        except (URLError, HTTPError) as e:
            self._check_api_error(e)

    #
    # Higher-level API methods
    #

    def api_get_blob(self, repo_api_url, path, sha):
        url = self._build_api_url(repo_api_url, 'git/blobs/%s' % sha)

        try:
            return self.http_get(url, headers={
                'Accept': self.RAW_MIMETYPE,
            })[0]
        except (URLError, HTTPError):
            raise FileNotFoundError(path, sha)

    def api_get_commits(self, repo_api_url, start=None):
        url = self._build_api_url(repo_api_url, 'commits')
        if start:
            url += '&sha=%s' % start

        try:
            return self.api_get(url)
        except Exception as e:
            logging.warning('Failed to fetch commits from %s: %s',
                            url, e, exc_info=1)
            raise SCMError(six.text_type(e))

    def api_get_compare_commits(self, repo_api_url, parent_revision, revision):
        # If the commit has a parent commit, use GitHub's "compare two commits"
        # API to get the diff. Otherwise, fetch the commit itself.
        if parent_revision:
            url = self._build_api_url(
                repo_api_url,
                'compare/%s...%s' % (parent_revision, revision))
        else:
            url = self._build_api_url(repo_api_url, 'commits/%s' % revision)

        try:
            comparison = self.api_get(url)
        except Exception as e:
            logging.warning('Failed to fetch commit comparison from %s: %s',
                            url, e, exc_info=1)
            raise SCMError(six.text_type(e))

        if parent_revision:
            tree_sha = comparison['base_commit']['commit']['tree']['sha']
        else:
            tree_sha = comparison['commit']['tree']['sha']

        return comparison['files'], tree_sha


    def api_get_heads(self, repo_api_url):
        url = self._build_api_url(repo_api_url, 'git/refs/heads')

        try:
            rsp = self.api_get(url)
            return [ref for ref in rsp if ref['ref'].startswith('refs/heads/')]
        except Exception as e:
            logging.warning('Failed to fetch commits from %s: %s',
                            url, e, exc_info=1)
            raise SCMError(six.text_type(e))

    def api_get_remote_repositories(self, api_url, owner, owner_type,
                                    filter_type=None, start=None,
                                    per_page=None):
        url = api_url

        if owner_type == 'organization':
            url += 'orgs/%s/repos' % owner
        elif owner_type == 'user':
            if owner == self.account.username:
                # All repositories belonging to an authenticated user.
                url += 'user/repos'
            else:
                # Only public repositories for the user.
                url += 'users/%s/repos' % owner
        else:
            raise ValueError(
                "owner_type must be 'organization' or 'user', not %r'"
                % owner_type)

        if filter_type:
            url += '?type=%s' % (filter_type or 'all')

        return self.api_get_list(self._build_api_url(url),
                                 start=start, per_page=per_page)

    def api_get_remote_repository(self, api_url, owner, repository_id):
        try:
            return self.api_get(self._build_api_url(
                '%srepos/%s/%s' % (api_url, owner, repository_id)))
        except HostingServiceError as e:
            if e.http_code == 404:
                return None
            else:
                raise

    def api_get_tree(self, repo_api_url, sha, recursive=False):
        url = self._build_api_url(repo_api_url, 'git/trees/%s' % sha)

        if recursive:
            url += '&recursive=1'

        try:
            return self.api_get(url)
        except Exception as e:
            logging.warning('Failed to fetch tree from %s: %s',
                            url, e, exc_info=1)
            raise SCMError(six.text_type(e))

    #
    # Internal utilities
    #

    def _build_api_url(self, *api_paths):
        url = '/'.join(api_paths)

        if '?' in url:
            url += '&'
        else:
            url += '?'

        url += 'access_token=%s' % self.account.data['authorization']['token']

        return url

    def _check_rate_limits(self, headers):
        rate_limit_remaining = headers.get('X-RateLimit-Remaining', None)

        try:
            if (rate_limit_remaining is not None and
                int(rate_limit_remaining) <= 100):
                logging.warning('GitHub rate limit for %s is down to %s',
                                self.account.username, rate_limit_remaining)
        except ValueError:
            pass

    def _check_api_error(self, e):
        data = e.read()

        try:
            rsp = json.loads(data)
        except:
            rsp = None

        if rsp and 'message' in rsp:
            response_info = e.info()
            x_github_otp = response_info.get('X-GitHub-OTP', '')

            if x_github_otp.startswith('required;'):
                raise TwoFactorAuthCodeRequiredError(
                    _('Enter your two-factor authentication code. '
                      'This code will be sent to you by GitHub.'),
                    http_code=e.code)

            if e.code == 401:
                raise AuthorizationError(rsp['message'], http_code=e.code)

            raise HostingServiceError(rsp['message'], http_code=e.code)
        else:
            raise HostingServiceError(six.text_type(e), http_code=e.code)


    supports_list_remote_repositories = True
    client_class = GitHubClient

            repo_info = self.client.api_get(
                self._build_api_url(
                    self._get_repo_api_url_raw(
                        self._get_repository_owner_raw(plan, kwargs),
                        self._get_repository_name_raw(plan, kwargs))))
        except HostingServiceError as e:
            if e.http_code == 404:
            rsp, headers = self.client.json_post(
                    # If we get a 404 Not Found, then the authorization was
                    if e.http_code != 404:
        repo_api_url = self._get_repo_api_url(repository)
        return self.client.api_get_blob(repo_api_url, path, revision)
            repo_api_url = self._get_repo_api_url(repository)
            self.client.api_get_blob(repo_api_url, path, revision)
        except FileNotFoundError:
        repo_api_url = self._get_repo_api_url(repository)
        refs = self.client.api_get_heads(repo_api_url)
        results = []
        for ref in refs:
            name = ref['ref'][len('refs/heads/'):]
            results.append(Branch(id=name,
                                  commit=ref['object']['sha'],
    def get_commits(self, repository, branch=None, start=None):
        repo_api_url = self._get_repo_api_url(repository)
        commits = self.client.api_get_commits(repo_api_url, start=start)
        results = []
        for item in commits:
            commit = self.client.api_get_commits(repo_api_url, revision)[0]
        # Step 2: Get the diff and tree from the "compare commits" API
        files, tree_sha = self.client.api_get_compare_commits(
            repo_api_url, parent_revision, revision)
        tree = self.client.api_get_tree(repo_api_url, tree_sha, recursive=True)
    def get_remote_repositories(self, owner=None, owner_type='user',
                                filter_type=None, start=None, per_page=None):
        """Return a list of remote repositories matching the given criteria.

        This will look up each remote repository on GitHub that the given
        owner either owns or is a member of.

        If the plan is an organization plan, then `owner` is expected to be
        an organization name, and the resulting repositories with be ones
        either owned by that organization or that the organization is a member
        of, and can be accessed by the authenticated user.

        If the plan is a public or private plan, and `owner` is the current
        user, then that user's public and private repositories or ones
        they're a member of will be returned.

        Otherwise, `owner` is assumed to be another GitHub user, and their
        accessible repositories that they own or are a member of will be
        returned.

        `owner` defaults to the linked account's username, and `plan`
        defaults to 'public'.
        """
        if owner is None and owner_type == 'user':
            owner = self.account.username

        assert owner

        url = self.get_api_url(self.account.hosting_url)
        paginator = self.client.api_get_remote_repositories(
            url, owner, owner_type, filter_type, start, per_page)

        return ProxyPaginator(
            paginator,
            normalize_page_data_func=lambda page_data: [
                RemoteRepository(
                    self,
                    repository_id='%s/%s' % (repo['owner']['login'],
                                             repo['name']),
                    name=repo['name'],
                    owner=repo['owner']['login'],
                    scm_type='Git',
                    path=repo['clone_url'],
                    mirror_path=repo['mirror_url'],
                    extra_data=repo)
                for repo in page_data
            ])

    def get_remote_repository(self, repository_id):
        """Get the remote repository for the ID.

        The ID is expected to be an ID returned from get_remote_repositories(),
        in the form of "owner/repo_id".

        If the repository is not found, ObjectDoesNotExist will be raised.
        """
        parts = repository_id.split('/')
        repo = None

        if len(parts) == 2:
            repo = self.client.api_get_remote_repository(
                self.get_api_url(self.account.hosting_url),
                *parts)

        if not repo:
            raise ObjectDoesNotExist

        return RemoteRepository(self,
                                repository_id=repository_id,
                                name=repo['name'],
                                owner=repo['owner']['login'],
                                scm_type='Git',
                                path=repo['clone_url'],
                                mirror_path=repo['mirror_url'],
                                extra_data=repo)

        return self.client.api_post(url=url,
                                    username=client_id,
                                    password=client_secret)
        self.client.api_delete(url=url,
                               headers=headers,
                               username=self.account.username,
                               password=password)
        return self.client._build_api_url(*api_paths)