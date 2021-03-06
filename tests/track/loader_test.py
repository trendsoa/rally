import re
import unittest.mock as mock
from unittest import TestCase

import jinja2

from esrally import exceptions, config
from esrally.utils import io
from esrally.track import loader


def strip_ws(s):
    return re.sub(r"\s", "", s)


class StaticClock:
    NOW = 1453362707.0

    @staticmethod
    def now():
        return StaticClock.NOW

    @staticmethod
    def stop_watch():
        return None


class SimpleTrackRepositoryTests(TestCase):
    @mock.patch("os.path.exists")
    @mock.patch("os.path.isdir")
    def test_track_from_directory(self, is_dir, path_exists):
        is_dir.return_value = True
        path_exists.return_value = True

        repo = loader.SimpleTrackRepository("/path/to/track/unit-test")
        self.assertEqual("unit-test", repo.track_name)
        self.assertEqual(["unit-test"], repo.track_names)
        self.assertEqual("/path/to/track/unit-test", repo.track_dir("unit-test"))
        self.assertEqual("/path/to/track/unit-test/track.json", repo.track_file("unit-test"))

    @mock.patch("os.path.exists")
    @mock.patch("os.path.isdir")
    @mock.patch("os.path.isfile")
    def test_track_from_file(self, is_file, is_dir, path_exists):
        is_file.return_value = True
        is_dir.return_value = False
        path_exists.return_value = True

        repo = loader.SimpleTrackRepository("/path/to/track/unit-test/my-track.json")
        self.assertEqual("my-track", repo.track_name)
        self.assertEqual(["my-track"], repo.track_names)
        self.assertEqual("/path/to/track/unit-test", repo.track_dir("my-track"))
        self.assertEqual("/path/to/track/unit-test/my-track.json", repo.track_file("my-track"))

    @mock.patch("os.path.exists")
    @mock.patch("os.path.isdir")
    @mock.patch("os.path.isfile")
    def test_track_from_named_pipe(self, is_file, is_dir, path_exists):
        is_file.return_value = False
        is_dir.return_value = False
        path_exists.return_value = True

        with self.assertRaises(exceptions.SystemSetupError) as ctx:
            loader.SimpleTrackRepository("a named pipe cannot point to a track")
        self.assertEqual("a named pipe cannot point to a track is neither a file nor a directory", ctx.exception.args[0])

    @mock.patch("os.path.exists")
    def test_track_from_non_existing_path(self, path_exists):
        path_exists.return_value = False
        with self.assertRaises(FileNotFoundError) as ctx:
            loader.SimpleTrackRepository("/path/does/not/exist")
        self.assertEqual("Track path /path/does/not/exist does not exist", ctx.exception.args[0])

    @mock.patch("os.path.isdir")
    @mock.patch("os.path.exists")
    def test_track_from_directory_without_track(self, path_exists, is_dir):
        # directory exists, but not the file
        path_exists.side_effect = [True, False]
        is_dir.return_value = True
        with self.assertRaises(FileNotFoundError) as ctx:
            loader.SimpleTrackRepository("/path/to/not/a/track")
        self.assertEqual("Could not find track.json in /path/to/not/a/track", ctx.exception.args[0])

    @mock.patch("os.path.exists")
    @mock.patch("os.path.isdir")
    @mock.patch("os.path.isfile")
    def test_track_from_file_but_not_json(self, is_file, is_dir, path_exists):
        is_file.return_value = True
        is_dir.return_value = False
        path_exists.return_value = True

        with self.assertRaises(exceptions.SystemSetupError) as ctx:
            loader.SimpleTrackRepository("/path/to/track/unit-test/my-track.xml")
        self.assertEqual("/path/to/track/unit-test/my-track.xml has to be a JSON file", ctx.exception.args[0])


class GitRepositoryTests(TestCase):
    class MockGitRepo:
        def __init__(self, remote_url, root_dir, repo_name, resource_name, offline, fetch=True):
            self.repo_dir = "%s/%s" % (root_dir, repo_name)

    @mock.patch("os.path.exists")
    @mock.patch("os.walk")
    def test_track_from_existing_repo(self, walk, exists):
        walk.return_value = iter([(".", ["unittest", "unittest2", "unittest3"], [])])
        exists.return_value = True
        cfg = config.Config()
        cfg.add(config.Scope.application, "track", "track.name", "unittest")
        cfg.add(config.Scope.application, "track", "repository.name", "default")
        cfg.add(config.Scope.application, "system", "offline.mode", False)
        cfg.add(config.Scope.application, "node", "root.dir", "/tmp")
        cfg.add(config.Scope.application, "benchmarks", "track.repository.dir", "tracks")

        repo = loader.GitTrackRepository(cfg, fetch=False, update=False, repo_class=GitRepositoryTests.MockGitRepo)

        self.assertEqual("unittest", repo.track_name)
        self.assertEqual(["unittest", "unittest2", "unittest3"], list(repo.track_names))
        self.assertEqual("/tmp/tracks/default/unittest", repo.track_dir("unittest"))
        self.assertEqual("/tmp/tracks/default/unittest/track.json", repo.track_file("unittest"))


class TemplateRenderTests(TestCase):
    def test_render_simple_template(self):
        template = """
        {
            "key": {{'01-01-2000' | days_ago(now)}},
            "key2": "static value"
        }
        """

        rendered = loader.render_template(
            loader=jinja2.DictLoader({"unittest": template}), template_name="unittest", clock=StaticClock)

        expected = """
        {
            "key": 5864,
            "key2": "static value"
        }
        """
        self.assertEqual(expected, rendered)

    def test_render_template_with_globbing(self):
        def key_globber(e):
            if e == "dynamic-key-*":
                return [
                    "dynamic-key-1",
                    "dynamic-key-2",
                    "dynamic-key-3",
                ]
            else:
                return []

        template = """
        {% import "rally.helpers" as rally %}
        {
            "key1": "static value",
            {{ rally.collect(parts="dynamic-key-*") }}

        }
        """

        rendered = loader.render_template(
            loader=jinja2.DictLoader(
                {
                    "unittest": template,
                    "dynamic-key-1": '"dkey1": "value1"',
                    "dynamic-key-2": '"dkey2": "value2"',
                    "dynamic-key-3": '"dkey3": "value3"',
                 }),
            template_name="unittest", glob_helper=key_globber, clock=StaticClock)

        expected = """
        {
            "key1": "static value",
            "dkey1": "value1",
            "dkey2": "value2",
            "dkey3": "value3"

        }
        """
        self.assertEqualIgnoreWhitespace(expected, rendered)

    def test_render_template_with_variables(self):
        def key_globber(e):
            if e == "dynamic-key-*":
                return ["dynamic-key-1"]
            else:
                return []

        template = """
        {% set clients = 16 %}
        {% import "rally.helpers" as rally with context %}
        {
            "key1": "static value",
            {{ rally.collect(parts="dynamic-key-*") }}

        }
        """
        rendered = loader.render_template(
            loader=jinja2.DictLoader(
                {
                    "unittest": template,
                    "dynamic-key-1": '"dkey1": {{ clients }}',
                 }),
            template_name="unittest", glob_helper=key_globber, clock=StaticClock)

        expected = """
        {
            "key1": "static value",
            "dkey1": 16
        }
        """
        self.assertEqualIgnoreWhitespace(expected, rendered)

    def assertEqualIgnoreWhitespace(self, expected, actual):
        self.assertEqual(strip_ws(expected), strip_ws(actual))


class TrackPostProcessingTests(TestCase):
    def test_post_processes_track_spec(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [
                {
                    "name": "test-index",
                    "types": [
                        {
                            "name": "test-type",
                            "documents": "documents.json.bz2",
                            "document-count": 10,
                            "compressed-bytes": 100,
                            "uncompressed-bytes": 10000,
                            "mapping": "type-mappings.json"
                        }
                    ]
                }
            ],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index",
                    "bulk-size": 5000
                },
                {
                    "name": "search",
                    "operation-type": "search"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "index-settings": {},
                    "schedule": [
                        {
                            "clients": 8,
                            "operation": "index-append",
                            "warmup-time-period": 100,
                            "time-period": 240,
                        },
                        {
                            "parallel": {
                                "tasks": [
                                    {
                                        "clients": 4,
                                        "operation": "search",
                                        "warmup-iterations": 1000,
                                        "iterations": 2000
                                    },
                                    {
                                        "clients": 1,
                                        "operation": "search",
                                        "warmup-iterations": 1000,
                                        "iterations": 2000
                                    },
                                    {
                                        "clients": 1,
                                        "operation": "search",
                                        "iterations": 1
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

        expected_post_processed = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [
                {
                    "name": "test-index",
                    "types": [
                        {
                            "name": "test-type",
                            "documents": "documents.json.bz2",
                            "document-count": 10,
                            "compressed-bytes": 100,
                            "uncompressed-bytes": 10000,
                            "mapping": "type-mappings.json"
                        }
                    ]
                }
            ],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index",
                    "bulk-size": 5000
                },
                {
                    "name": "search",
                    "operation-type": "search"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "index-settings": {},
                    "schedule": [
                        {
                            "clients": 8,
                            "operation": "index-append",
                            "warmup-time-period": 0,
                            "time-period": 10,
                        },
                        {
                            "parallel": {
                                "tasks": [
                                    {
                                        "clients": 4,
                                        "operation": "search",
                                        "warmup-iterations": 4,
                                        "iterations": 4
                                    },
                                    {
                                        "clients": 1,
                                        "operation": "search",
                                        "warmup-iterations": 1,
                                        "iterations": 1
                                    },
                                    {
                                        "clients": 1,
                                        "operation": "search",
                                        "iterations": 1
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }

        self.assertEqual(self.as_track(expected_post_processed),
                         loader.post_process_for_test_mode(self.as_track(track_specification)))

    def as_track(self, track_specification):
        reader = loader.TrackSpecificationReader(source=io.DictStringFileSourceFactory({
            "/mappings/type-mappings.json": ['{"test-type": "empty-for-test"}']
        }))
        return reader("unittest", track_specification, "/mappings")


class TrackPathTests(TestCase):
    def test_sets_absolute_path(self):
        from esrally import config
        from esrally.track import track

        cfg = config.Config()
        cfg.add(config.Scope.application, "benchmarks", "local.dataset.cache", "/data")

        default_challenge = track.Challenge("default", description="default challenge", default=True, schedule=[
            track.Task(operation=track.Operation("index", operation_type=track.OperationType.Index), clients=4)
        ])
        another_challenge = track.Challenge("other", description="non-default challenge", default=False)
        t = track.Track(name="unittest", short_description="unittest track", challenges=[another_challenge, default_challenge],
                        indices=[
                            track.Index(name="test",
                                        auto_managed=True,
                                        types=[track.Type("docs",
                                                          mapping={},
                                                          document_file="docs/documents.json",
                                                          document_archive="docs/documents.json.bz2")])
                        ])

        loader.set_absolute_data_path(cfg, t)

        self.assertEqual("/data/docs/documents.json", t.indices[0].types[0].document_file)
        self.assertEqual("/data/docs/documents.json.bz2", t.indices[0].types[0].document_archive)


class TrackFilterTests(TestCase):
    def test_create_filters_from_empty_included_tasks(self):
        self.assertEqual(0, len(loader.filters_from_included_tasks(None)))
        self.assertEqual(0, len(loader.filters_from_included_tasks([])))

    def test_create_filters_from_mixed_included_tasks(self):
        from esrally.track import track
        filters = loader.filters_from_included_tasks(["force-merge", "type:search"])
        self.assertListEqual([track.TaskOpNameFilter("force-merge"), track.TaskOpTypeFilter("search")], filters)

    def test_rejects_invalid_syntax(self):
        with self.assertRaises(exceptions.SystemSetupError) as ctx:
            loader.filters_from_included_tasks(["valid", "a:b:c"])
        self.assertEqual("Invalid format for included tasks: [a:b:c]", ctx.exception.args[0])

    def test_rejects_unknown_filter_type(self):
        with self.assertRaises(exceptions.SystemSetupError) as ctx:
            loader.filters_from_included_tasks(["valid", "op-type:index"])
        self.assertEqual("Invalid format for included tasks: [op-type:index]. Expected [type] but got [op-type].", ctx.exception.args[0])

    def test_filters_tasks(self):
        from esrally.track import track
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-1",
                    "operation-type": "index"
                },
                {
                    "name": "index-2",
                    "operation-type": "index"
                },
                {
                    "name": "index-3",
                    "operation-type": "index"
                },
                {
                    "name": "node-stats",
                    "operation-type": "node-stats"
                },
                {
                    "name": "cluster-stats",
                    "operation-type": "custom-operation-type"
                },
                {
                    "name": "match-all",
                    "operation-type": "search",
                    "body": {
                        "query": {
                            "match_all": {}
                        }
                    }
                },
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "schedule": [
                        {
                            "parallel": {
                                "tasks": [
                                    {
                                        "operation": "index-1",
                                    },
                                    {
                                        "operation": "index-2",
                                    },
                                    {
                                        "operation": "index-3",
                                    },
                                    {
                                        "operation": "match-all",
                                    },
                                ]
                            }
                        },
                        {
                            "operation": "node-stats"
                        },
                        {
                            "operation": "match-all"
                        },
                        {
                            "operation": "cluster-stats"
                        }
                    ]
                }
            ]
        }
        reader = loader.TrackSpecificationReader()
        full_track = reader("unittest", track_specification, "/mappings")
        self.assertEqual(4, len(full_track.challenges[0].schedule))

        filtered = loader.filter_included_tasks(full_track, [track.TaskOpNameFilter("index-3"),
                                                             track.TaskOpTypeFilter("search"),
                                                             # Filtering should also work for non-core operation types.
                                                             track.TaskOpTypeFilter("custom-operation-type")
                                                             ])

        schedule = filtered.challenges[0].schedule
        self.assertEqual(3, len(schedule))
        self.assertEqual(["index-3", "match-all"], [t.operation.name for t in schedule[0].tasks])
        self.assertEqual("match-all", schedule[1].operation.name)
        self.assertEqual("cluster-stats", schedule[2].operation.name)


class TrackSpecificationReaderTests(TestCase):
    def test_missing_description_raises_syntax_error(self):
        track_specification = {
            "description": "unittest track"
        }
        reader = loader.TrackSpecificationReader()
        with self.assertRaises(loader.TrackSyntaxError) as ctx:
            reader("unittest", track_specification, "/mappings")
        self.assertEqual("Track 'unittest' is invalid. Mandatory element 'short-description' is missing.", ctx.exception.args[0])

    def test_can_read_track_info(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "data-url": "https://localhost/data",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [],
            "challenges": []
        }
        reader = loader.TrackSpecificationReader()
        resulting_track = reader("unittest", track_specification, "/mappings")
        self.assertEqual("unittest", resulting_track.name)
        self.assertEqual("short description for unit test", resulting_track.short_description)
        self.assertEqual("longer description of this track for unit test", resulting_track.description)
        self.assertEqual("https://localhost/data", resulting_track.source_root_url)

    def test_document_count_mandatory_if_file_present(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "data-url": "https://localhost/data",
            "indices": [{"name": "test-index", "types": [{"name": "docs", "documents": "documents.json.bz2"}]}],
            "operations": [],
            "challenges": []
        }
        reader = loader.TrackSpecificationReader()
        with self.assertRaises(loader.TrackSyntaxError) as ctx:
            reader("unittest", track_specification, "/mappings")
        self.assertEqual("Track 'unittest' is invalid. Mandatory element 'document-count' is missing.", ctx.exception.args[0])

    def test_parse_with_mixed_warmup_iterations_and_measurement(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "data-url": "https://localhost/data",
            "indices": [
                {
                    "name": "test-index",
                    "types": [
                        {
                            "name": "main",
                            "documents": "documents-main.json.bz2",
                            "document-count": 10,
                            "compressed-bytes": 100,
                            "uncompressed-bytes": 10000,
                            "mapping": "main-type-mappings.json"
                        }
                    ]
                }
            ],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index",
                    "bulk-size": 5000,
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "index-settings": {},
                    "schedule": [
                        {
                            "clients": 8,
                            "operation": "index-append",
                            "warmup-iterations": 3,
                            "time-period": 60
                        }
                    ]
                }

            ]
        }

        reader = loader.TrackSpecificationReader(source=io.DictStringFileSourceFactory({
            "/mappings/main-type-mappings.json": ['{"main": "empty-for-test"}'],
        }))
        with self.assertRaises(loader.TrackSyntaxError) as ctx:
            reader("unittest", track_specification, "/mappings")
        self.assertEqual("Track 'unittest' is invalid. Operation 'index-append' in challenge 'default-challenge' defines '3' warmup "
                         "iterations and a time period of '60' seconds. Please do not mix time periods and iterations.",
                         ctx.exception.args[0])

    def test_parse_with_mixed_warmup_time_period_and_iterations(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "data-url": "https://localhost/data",
            "indices": [
                {
                    "name": "test-index",
                    "types": [
                        {
                            "name": "main",
                            "documents": "documents-main.json.bz2",
                            "document-count": 10,
                            "compressed-bytes": 100,
                            "uncompressed-bytes": 10000,
                            "mapping": "main-type-mappings.json"
                        }
                    ]
                }
            ],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index",
                    "bulk-size": 5000,
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "index-settings": {},
                    "schedule": [
                        {
                            "clients": 8,
                            "operation": "index-append",
                            "warmup-time-period": 20,
                            "iterations": 1000
                        }
                    ]
                }

            ]
        }

        reader = loader.TrackSpecificationReader(source=io.DictStringFileSourceFactory({
            "/mappings/main-type-mappings.json": ['{"main": "empty-for-test"}'],
        }))
        with self.assertRaises(loader.TrackSyntaxError) as ctx:
            reader("unittest", track_specification, "/mappings")
        self.assertEqual("Track 'unittest' is invalid. Operation 'index-append' in challenge 'default-challenge' defines a warmup time "
                         "period of '20' seconds and '1000' iterations. Please do not mix time periods and iterations.",
                         ctx.exception.args[0])

    def test_parse_valid_track_specification(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "data-url": "https://localhost/data",
            "indices": [
                {
                    "name": "index-historical",
                    "types": [
                        {
                            "name": "main",
                            "documents": "documents-main.json.bz2",
                            "document-count": 10,
                            "compressed-bytes": 100,
                            "uncompressed-bytes": 10000,
                            "mapping": "main-type-mappings.json"
                        },
                        {
                            "name": "secondary",
                            "documents": "documents-secondary.json.bz2",
                            "includes-action-and-meta-data": True,
                            "document-count": 20,
                            "compressed-bytes": 200,
                            "uncompressed-bytes": 20000,
                            "mapping": "secondary-type-mappings.json"
                        }

                    ]
                }
            ],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index",
                    "bulk-size": 5000,
                    "meta": {
                        "append": True
                    }
                },
                {
                    "name": "search",
                    "operation-type": "search",
                    "index": "index-historical"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "meta": {
                        "mixed": True,
                        "max-clients": 8
                    },
                    "index-settings": {
                        "index.number_of_replicas": 2
                    },
                    "schedule": [
                        {
                            "clients": 8,
                            "operation": "index-append",
                            "meta": {
                                "operation-index": 0
                            }
                        },
                        {
                            "clients": 1,
                            "operation": "search"
                        }
                    ]
                }

            ]
        }
        reader = loader.TrackSpecificationReader(source=io.DictStringFileSourceFactory({
            "/mappings/main-type-mappings.json": ['{"main": "empty-for-test"}'],
            "/mappings/secondary-type-mappings.json": ['{"secondary": "empty-for-test"}'],
        }))
        resulting_track = reader("unittest", track_specification, "/mappings")
        self.assertEqual("unittest", resulting_track.name)
        self.assertEqual("short description for unit test", resulting_track.short_description)
        self.assertEqual("longer description of this track for unit test", resulting_track.description)
        self.assertEqual(1, len(resulting_track.indices))
        self.assertEqual("index-historical", resulting_track.indices[0].name)
        self.assertEqual(2, len(resulting_track.indices[0].types))
        self.assertEqual("main", resulting_track.indices[0].types[0].name)
        self.assertFalse(resulting_track.indices[0].types[0].includes_action_and_meta_data)
        self.assertEqual("unittest/documents-main.json.bz2", resulting_track.indices[0].types[0].document_archive)
        self.assertEqual("unittest/documents-main.json", resulting_track.indices[0].types[0].document_file)
        self.assertDictEqual({"main": "empty-for-test"}, resulting_track.indices[0].types[0].mapping)
        self.assertEqual("secondary", resulting_track.indices[0].types[1].name)
        self.assertDictEqual({"secondary": "empty-for-test"}, resulting_track.indices[0].types[1].mapping)
        self.assertTrue(resulting_track.indices[0].types[1].includes_action_and_meta_data)
        self.assertEqual(1, len(resulting_track.challenges))
        self.assertEqual("default-challenge", resulting_track.challenges[0].name)
        self.assertEqual(1, len(resulting_track.challenges[0].index_settings))
        self.assertEqual(2, resulting_track.challenges[0].index_settings["index.number_of_replicas"])
        self.assertEqual({"mixed": True, "max-clients": 8}, resulting_track.challenges[0].meta_data)
        self.assertEqual({"append": True}, resulting_track.challenges[0].schedule[0].operation.meta_data)
        self.assertEqual({"operation-index": 0}, resulting_track.challenges[0].schedule[0].meta_data)

    def test_parse_valid_track_specification_with_index_template(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "templates": [
                {
                    "name": "my-index-template",
                    "index-pattern": "*",
                    "template": "default-template.json"
                }
            ],
            "operations": [],
            "challenges": []
        }
        reader = loader.TrackSpecificationReader(source=io.DictStringFileSourceFactory({
            "/mappings/default-template.json": ['{"some-index-template": "empty-for-test"}'],
        }))
        resulting_track = reader("unittest", track_specification, "/mappings")
        self.assertEqual("unittest", resulting_track.name)
        self.assertEqual("short description for unit test", resulting_track.short_description)
        self.assertEqual("longer description of this track for unit test", resulting_track.description)
        self.assertEqual(0, len(resulting_track.indices))
        self.assertEqual(1, len(resulting_track.templates))
        self.assertEqual("my-index-template", resulting_track.templates[0].name)
        self.assertEqual("*", resulting_track.templates[0].pattern)
        self.assertEqual({"some-index-template": "empty-for-test"}, resulting_track.templates[0].content)
        self.assertEqual(0, len(resulting_track.challenges))

    def test_types_are_optional_for_user_managed_indices(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [],
            "challenges": []
        }
        reader = loader.TrackSpecificationReader()
        resulting_track = reader("unittest", track_specification, "/mappings")
        self.assertEqual("unittest", resulting_track.name)
        self.assertEqual("short description for unit test", resulting_track.short_description)
        self.assertEqual("longer description of this track for unit test", resulting_track.description)
        self.assertEqual(1, len(resulting_track.indices))
        self.assertEqual(0, len(resulting_track.templates))
        self.assertEqual("test-index", resulting_track.indices[0].name)
        self.assertEqual(0, len(resulting_track.indices[0].types))

    def test_unique_challenge_names(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "test-challenge",
                    "description": "Some challenge",
                    "default": True,
                    "schedule": [
                        {
                            "operation": "index-append"
                        }
                    ]
                },
                {
                    "name": "test-challenge",
                    "description": "Another challenge with the same name",
                    "schedule": [
                        {
                            "operation": "index-append"
                        }
                    ]
                }

            ]
        }
        reader = loader.TrackSpecificationReader()
        with self.assertRaises(loader.TrackSyntaxError) as ctx:
            reader("unittest", track_specification, "/mappings")
        self.assertEqual("Track 'unittest' is invalid. Duplicate challenge with name 'test-challenge'.", ctx.exception.args[0])

    def test_not_more_than_one_default_challenge_possible(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "default": True,
                    "schedule": [
                        {
                            "operation": "index-append"
                        }
                    ]
                },
                {
                    "name": "another-challenge",
                    "description": "See if we can sneek it in as another default",
                    "default": True,
                    "schedule": [
                        {
                            "operation": "index-append"
                        }
                    ]
                }

            ]
        }
        reader = loader.TrackSpecificationReader()
        with self.assertRaises(loader.TrackSyntaxError) as ctx:
            reader("unittest", track_specification, "/mappings")
        self.assertEqual("Track 'unittest' is invalid. Both 'default-challenge' and 'another-challenge' are defined as default challenges. "
                         "Please define only one of them as default.", ctx.exception.args[0])

    def test_at_least_one_default_challenge(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "challenge",
                    "description": "Some challenge",
                    "schedule": [
                        {
                            "operation": "index-append"
                        }
                    ]
                },
                {
                    "name": "another-challenge",
                    "description": "Another challenge",
                    "schedule": [
                        {
                            "operation": "index-append"
                        }
                    ]
                }

            ]
        }
        reader = loader.TrackSpecificationReader()
        with self.assertRaises(loader.TrackSyntaxError) as ctx:
            reader("unittest", track_specification, "/mappings")
        self.assertEqual("Track 'unittest' is invalid. No default challenge specified. Please edit the track and add \"default\": true "
                         "to one of the challenges challenge, another-challenge.", ctx.exception.args[0])

    def test_exactly_one_default_challenge(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "challenge",
                    "description": "Some challenge",
                    "default": True,
                    "schedule": [
                        {
                            "operation": "index-append"
                        }
                    ]
                },
                {
                    "name": "another-challenge",
                    "description": "Another challenge",
                    "schedule": [
                        {
                            "operation": "index-append"
                        }
                    ]
                }

            ]
        }
        reader = loader.TrackSpecificationReader()
        resulting_track = reader("unittest", track_specification, "/mappings")
        self.assertEqual(2, len(resulting_track.challenges))
        self.assertEqual("challenge", resulting_track.challenges[0].name)
        self.assertTrue(resulting_track.challenges[0].default)
        self.assertFalse(resulting_track.challenges[1].default)

    def test_selects_sole_challenge_implicitly_as_default(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "challenge",
                    "description": "Some challenge",
                    "schedule": [
                        {
                            "operation": "index-append"
                        }
                    ]
                }
            ]
        }
        reader = loader.TrackSpecificationReader()
        resulting_track = reader("unittest", track_specification, "/mappings")
        self.assertEqual(1, len(resulting_track.challenges))
        self.assertEqual("challenge", resulting_track.challenges[0].name)
        self.assertTrue(resulting_track.challenges[0].default)

    def test_supports_target_throughput(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "schedule": [
                        {
                            "operation": "index-append",
                            "target-throughput": 10,
                        }
                    ]
                }
            ]
        }
        reader = loader.TrackSpecificationReader()
        resulting_track = reader("unittest", track_specification, "/mappings")
        self.assertEqual(10, resulting_track.challenges[0].schedule[0].params["target-throughput"])

    def test_supports_target_interval(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-append",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "schedule": [
                        {
                            "operation": "index-append",
                            "target-interval": 5,
                        }
                    ]
                }
            ]
        }
        reader = loader.TrackSpecificationReader()
        resulting_track = reader("unittest", track_specification, "/mappings")
        self.assertEqual(5, resulting_track.challenges[0].schedule[0].params["target-interval"])

    def test_parallel_tasks_with_default_values(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-1",
                    "operation-type": "index"
                },
                {
                    "name": "index-2",
                    "operation-type": "index"
                },
                {
                    "name": "index-3",
                    "operation-type": "index"
                },
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "schedule": [
                        {
                            "parallel": {
                                "warmup-time-period": 2400,
                                "time-period": 36000,
                                "tasks": [
                                    {
                                        "operation": "index-1",
                                        "warmup-time-period": 300,
                                        "clients": 2
                                    },
                                    {
                                        "operation": "index-2",
                                        "time-period": 3600,
                                        "clients": 4
                                    },
                                    {
                                        "operation": "index-3",
                                        "target-throughput": 10,
                                        "clients": 16
                                    },
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        reader = loader.TrackSpecificationReader()
        resulting_track = reader("unittest", track_specification, "/mappings")
        parallel_element = resulting_track.challenges[0].schedule[0]
        parallel_tasks = parallel_element.tasks

        self.assertEqual(22, parallel_element.clients)
        self.assertEqual(3, len(parallel_tasks))

        self.assertEqual("index-1", parallel_tasks[0].operation.name)
        self.assertEqual(300, parallel_tasks[0].warmup_time_period)
        self.assertEqual(36000, parallel_tasks[0].time_period)
        self.assertEqual(2, parallel_tasks[0].clients)
        self.assertFalse("target-throughput" in parallel_tasks[0].params)

        self.assertEqual("index-2", parallel_tasks[1].operation.name)
        self.assertEqual(2400, parallel_tasks[1].warmup_time_period)
        self.assertEqual(3600, parallel_tasks[1].time_period)
        self.assertEqual(4, parallel_tasks[1].clients)
        self.assertFalse("target-throughput" in parallel_tasks[1].params)

        self.assertEqual("index-3", parallel_tasks[2].operation.name)
        self.assertEqual(2400, parallel_tasks[2].warmup_time_period)
        self.assertEqual(36000, parallel_tasks[2].time_period)
        self.assertEqual(16, parallel_tasks[2].clients)
        self.assertEqual(10, parallel_tasks[2].params["target-throughput"])

    def test_parallel_tasks_with_default_clients_does_not_propagate(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-1",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "schedule": [
                        {
                            "parallel": {
                                "warmup-time-period": 2400,
                                "time-period": 36000,
                                "clients": 2,
                                "tasks": [
                                    {
                                        "operation": "index-1"
                                    },
                                    {
                                        "operation": "index-1"
                                    },
                                    {
                                        "operation": "index-1"
                                    },
                                    {
                                        "operation": "index-1"
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        reader = loader.TrackSpecificationReader()
        resulting_track = reader("unittest", track_specification, "/mappings")
        parallel_element = resulting_track.challenges[0].schedule[0]
        parallel_tasks = parallel_element.tasks

        # we will only have two clients *in total*
        self.assertEqual(2, parallel_element.clients)
        self.assertEqual(4, len(parallel_tasks))
        for task in parallel_tasks:
            self.assertEqual(1, task.clients)

    def test_parallel_tasks_with_completed_by_set(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-1",
                    "operation-type": "index"
                },
                {
                    "name": "index-2",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "schedule": [
                        {
                            "parallel": {
                                "warmup-time-period": 2400,
                                "time-period": 36000,
                                "completed-by": "index-2",
                                "tasks": [
                                    {
                                        "operation": "index-1"
                                    },
                                    {
                                        "operation": "index-2"
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        reader = loader.TrackSpecificationReader()
        resulting_track = reader("unittest", track_specification, "/mappings")
        parallel_element = resulting_track.challenges[0].schedule[0]
        parallel_tasks = parallel_element.tasks

        # we will only have two clients *in total*
        self.assertEqual(2, parallel_element.clients)
        self.assertEqual(2, len(parallel_tasks))

        self.assertEqual("index-1", parallel_tasks[0].operation.name)
        self.assertFalse(parallel_tasks[0].completes_parent)

        self.assertEqual("index-2", parallel_tasks[1].operation.name)
        self.assertTrue(parallel_tasks[1].completes_parent)

    def test_parallel_tasks_with_completed_by_set_no_task_matches(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-1",
                    "operation-type": "index"
                },
                {
                    "name": "index-2",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "schedule": [
                        {
                            "parallel": {
                                "completed-by": "non-existing-task",
                                "tasks": [
                                    {
                                        "operation": "index-1"
                                    },
                                    {
                                        "operation": "index-2"
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        reader = loader.TrackSpecificationReader()

        with self.assertRaises(loader.TrackSyntaxError) as ctx:
            reader("unittest", track_specification, "/mappings")
        self.assertEqual("Track 'unittest' is invalid. 'parallel' element for challenge 'default-challenge' is marked with 'completed-by' "
                         "with task name 'non-existing-task' but no task with this name exists.", ctx.exception.args[0])

    def test_parallel_tasks_with_completed_by_set_multiple_tasks_match(self):
        track_specification = {
            "short-description": "short description for unit test",
            "description": "longer description of this track for unit test",
            "indices": [{"name": "test-index", "auto-managed": False}],
            "operations": [
                {
                    "name": "index-1",
                    "operation-type": "index"
                }
            ],
            "challenges": [
                {
                    "name": "default-challenge",
                    "description": "Default challenge",
                    "schedule": [
                        {
                            "parallel": {
                                "completed-by": "index-1",
                                "tasks": [
                                    {
                                        "operation": "index-1"
                                    },
                                    {
                                        "operation": "index-1"
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
        reader = loader.TrackSpecificationReader()

        with self.assertRaises(loader.TrackSyntaxError) as ctx:
            reader("unittest", track_specification, "/mappings")
        self.assertEqual("Track 'unittest' is invalid. 'parallel' element for challenge 'default-challenge' contains multiple tasks with "
                         "the name 'index-1' which are marked with 'completed-by' but only task is allowed to match.", ctx.exception.args[0])
