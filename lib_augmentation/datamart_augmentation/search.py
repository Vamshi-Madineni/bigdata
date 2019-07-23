import logging

from datamart_core.common import Type
from .utils import compute_levenshtein_sim

logger = logging.getLogger(__name__)

PAGINATION_SIZE = 100
TOP_K_SIZE = 50


def get_column_index_mapping(data_profile):
    """
    Get the mapping between column name and column index.

    :param data_profile: Profiled input dataset.
    :return: dict, where the key is the column name, and value is the column index
    """

    column_index = -1
    column_index_mapping = dict()
    for column in data_profile['columns']:
        column_index += 1
        column_index_mapping[column['name']] = column_index
    return column_index_mapping


def get_column_coverage(data_profile, column_index_mapping, filter_=()):
    """
    Get coverage for each column of the input dataset.

    :param data_profile: Profiled input dataset, if dataset is not in DataMart index.
    :param column_index_mapping: mapping from column name to column index
    :param filter_: list of column indices to return. If an empty list, return all the columns.
    :return: dict, where key is the column index, and value is a dict as follows:

        {
            'type': column meta-type ('structural_type', 'semantic_types', 'spatial'),
            'type_value': column type,
            'ranges': list of ranges
        }
    """

    column_coverage = dict()

    for column in data_profile['columns']:
        column_name = column['name']
        column_index = column_index_mapping[column_name]
        if 'coverage' not in column:
            continue
        if filter_ and column_index not in filter_:
            continue
        # ignoring 'd3mIndex'
        if 'd3mIndex' in column_name:
            continue
        if Type.ID in column['semantic_types']:
            type_ = 'semantic_types'
            type_value = Type.ID
        elif column['structural_type'] == Type.INTEGER:
            type_ = 'structural_type'
            type_value = column['structural_type']
        elif Type.DATE_TIME in column['semantic_types']:
            type_ = 'semantic_types'
            type_value = Type.DATE_TIME
        else:
            continue
        column_coverage[str(column_index)] = {
            'type':       type_,
            'type_value': type_value,
            'ranges':     []
        }
        for range_ in column['coverage']:
            column_coverage[str(column_index)]['ranges'].\
                append([float(range_['range']['gte']),
                        float(range_['range']['lte'])])

    if 'spatial_coverage' in data_profile:
        for spatial in data_profile['spatial_coverage']:
            if filter_ and (
                    column_index_mapping[spatial['lat']] not in filter_ or
                    column_index_mapping[spatial['lon']] not in filter_):
                continue
            names = (str(column_index_mapping[spatial['lat']]) + ',' +
                     str(column_index_mapping[spatial['lon']]))
            column_coverage[names] = {
                'type':      'spatial',
                'type_value': Type.LATITUDE + ',' + Type.LONGITUDE,
                'ranges':     []
            }
            for range_ in spatial['ranges']:
                column_coverage[names]['ranges'].\
                    append(range_['range']['coordinates'])

    return column_coverage


def get_lazo_sketches(data_profile, column_index_mapping, filter_=[]):
    """
    Get Lazo sketches of the input dataset, if available.

    :param data_profile: Profiled input dataset.
    :param filter_: list of column indices to return.
       If an empty list, return all the columns.
    :param: column_index_mapping: mapping from column name to column index
    :return: dict, where key is the column index, and value is a tuple
        (n_permutations, hash_values, cardinality)
    """

    lazo_sketches = dict()

    if 'lazo' in data_profile and data_profile['lazo']:
        for column in data_profile['lazo']:
            column_name = column['name']
            column_index = column_index_mapping[column_name]
            if filter_ and column_index not in filter_:
                continue
            lazo_sketches[str(column_index)] = (
                column['n_permutations'],
                column['hash_values'],
                column['cardinality']
            )

    return lazo_sketches


def get_numerical_join_search_results(es, type_, type_value, pivot_column, ranges,
                                      dataset_id=None, query_args=None):
    """Retrieve numerical join search results that intersect with the input numerical ranges.
    """

    filter_query = [{'term': {'%s' % type_: type_value}}]
    if dataset_id:
        filter_query.append(
            {'term': {'dataset_id': dataset_id}}
        )
    if type_value != Type.DATE_TIME:
        filter_query.append(
            {'fuzzy': {'name.raw': pivot_column}}
        )

    should_query = list()
    coverage = sum([range_[1] - range_[0] + 1 for range_ in ranges])
    for range_ in ranges:
        should_query.append({
            'nested': {
                'path': 'coverage',
                'query': {
                    'function_score': {
                        'query': {
                            'range': {
                                'coverage.range': {
                                    'gte': range_[0],
                                    'lte': range_[1],
                                    'relation': 'intersects'
                                }
                            }
                        },
                        'script_score': {
                            'script': {
                                'params': {
                                    'gte': range_[0],
                                    'lte': range_[1],
                                    'coverage': coverage
                                },
                                'source': '''
                                double start = Math.max(params.gte, doc['coverage.gte'].value);
                                double end = Math.min(params.lte, doc['coverage.lte'].value);
                                return (end - start + 1) / params.coverage;'''
                            }
                        },
                        'boost_mode': 'replace'
                    }
                },
                'inner_hits': {
                    '_source': False,
                    'size': 100
                },
                'score_mode': 'sum'
            }
        })

    body = {
        '_source': {
            'excludes': [
                'dataset_name',
                'dataset_description',
                'coverage',
                'mean',
                'stddev',
                'structural_type',
                'semantic_types'
            ]
        },
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'filter': filter_query,
                        'should': should_query,
                        'minimum_should_match': 1
                    }
                },
                'functions': [] if not query_args else query_args,
                'score_mode': 'sum',
                'boost_mode': 'multiply'
            }
        }
    }

    # logger.info("Query (numerical): %r", body)

    return es.search(
        index='datamart_columns',
        body=body,
        from_=0,
        size=TOP_K_SIZE
    )['hits']['hits']


def get_spatial_join_search_results(es, ranges, dataset_id=None,
                                    query_args=None):
    """Retrieve spatial join search results that intersect
    with the input spatial ranges.
    """

    filter_query = list()
    if dataset_id:
        filter_query.append(
            {'term': {'dataset_id': dataset_id}}
        )

    should_query = list()
    coverage = sum([
        (range_[1][0] - range_[0][0]) * (range_[0][1] - range_[1][1])
        for range_ in ranges])
    for range_ in ranges:
        should_query.append({
            'nested': {
                'path': 'ranges',
                'query': {
                    'function_score': {
                        'query': {
                            'geo_shape': {
                                'ranges.range': {
                                    'shape': {
                                        'type': 'envelope',
                                        'coordinates': [
                                            [range_[0][0], range_[0][1]],
                                            [range_[1][0], range_[1][1]]
                                        ]
                                    },
                                    'relation': 'intersects'
                                }
                            }
                        },
                        'script_score': {
                            'script': {
                                'params': {
                                    'min_lon': range_[0][0],
                                    'max_lat': range_[0][1],
                                    'max_lon': range_[1][0],
                                    'min_lat': range_[1][1],
                                    'coverage': coverage
                                },
                                'source': '''
                                double n_min_lon = Math.max(doc['ranges.min_lon'].value, params.min_lon);
                                double n_max_lat = Math.min(doc['ranges.max_lat'].value, params.max_lat);
                                double n_max_lon = Math.min(doc['ranges.max_lon'].value, params.max_lon);
                                double n_min_lat = Math.max(doc['ranges.min_lat'].value, params.min_lat);
                                return ((n_max_lon - n_min_lon) * (n_max_lat - n_min_lat)) / params.coverage;'''
                            }
                        },
                        'boost_mode': 'replace'
                    }
                },
                'inner_hits': {
                    '_source': False,
                    'size': 100
                },
                'score_mode': 'sum'
            }
        })

    body = {
        '_source': {
            'excludes': [
                'name',
                'dataset_name',
                'dataset_description',
                'ranges'
            ]
        },
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'filter': filter_query,
                        'should': should_query,
                        'minimum_should_match': 1
                    }
                },
                'functions': [] if not query_args else query_args,
                'score_mode': 'sum',
                'boost_mode': 'multiply'
            }
        }
    }

    # logger.info("Query (spatial): %r", body)

    return es.search(
        index='datamart_spatial_coverage',
        body=body,
        from_=0,
        size=TOP_K_SIZE
    )['hits']['hits']


def get_textual_join_search_results(es, dataset_ids, column_names,
                                    lazo_scores, query_args=None):
    """Combine Lazo textual search results with Elasticsearch
    (keyword search).
    """

    scores_per_dataset = dict()
    column_per_dataset = dict()
    for i in range(len(dataset_ids)):
        if dataset_ids[i] not in column_per_dataset:
            column_per_dataset[dataset_ids[i]] = list()
            scores_per_dataset[dataset_ids[i]] = dict()
        column_per_dataset[dataset_ids[i]].append(column_names[i])
        scores_per_dataset[dataset_ids[i]][column_names[i]] = lazo_scores[i]

    # if there is no keyword query
    if not query_args:
        results = list()
        for dataset_id in column_per_dataset:
            column_indices = get_column_identifiers(
                es=es,
                column_names=column_per_dataset[dataset_id],
                dataset_id=dataset_id
            )
            for j in range(len(column_indices)):
                column_name = column_per_dataset[dataset_id][j]
                results.append(
                    dict(
                        _score=scores_per_dataset[dataset_id][column_name],
                        _source=dict(
                            dataset_id=dataset_id,
                            name=column_name,
                            index=column_indices[j]
                        )
                    )
                )
        return results

    # if there is a keyword query
    should_query = list()
    for i in range(len(dataset_ids)):
        should_query.append({
            'bool': {
                'must': [
                    {
                        'term': {
                            'dataset_id': dataset_ids[i]
                        }
                    },
                    {
                        'term': {
                            'name.raw': column_names[i]
                        }
                    }
                ]
            }
        })

    body = {
        '_source': {
            'excludes': [
                'dataset_name',
                'dataset_description',
                'coverage',
                'mean',
                'stddev',
                'structural_type',
                'semantic_types'
            ]
        },
        'query': {
            'function_score': {
                'query': {
                    'bool': {
                        'should': should_query,
                        'minimum_should_match': 1
                    }
                },
                'functions': query_args,
                'score_mode': 'sum',
                'boost_mode': 'replace'
            }
        }
    }

    # logger.info("Query (textual): %r", body)

    results = list()

    from_ = 0
    query_results = es.search(
        index='datamart_columns',
        body=body,
        from_=from_,
        size=PAGINATION_SIZE
    )

    size_ = len(query_results['hits']['hits'])
    while size_ > 0:
        for hit in query_results['hits']['hits']:
            # multiplying keyword query score with Lazo score
            dataset_id = hit['_source']['dataset_id']
            column_name = hit['_source']['name']
            hit['_score'] *= scores_per_dataset[dataset_id][column_name]
            results.append(hit)
        from_ += size_
        query_results = es.search(
            index='datamart_columns',
            body=body,
            from_=from_,
            size=PAGINATION_SIZE
        )
        size_ = len(query_results['hits']['hits'])

    return results


def get_column_identifiers(es, column_names, dataset_id=None, data_profile=None):
    column_indices = [-1 for _ in column_names]
    if not data_profile:
        columns = es.get('datamart', '_doc', id=dataset_id)['_source']['columns']
    else:
        columns = data_profile['columns']
    for i in range(len(columns)):
        for j in range(len(column_names)):
            if columns[i]['name'] == column_names[j]:
                column_indices[j] = i
    return column_indices


def get_dataset_metadata(es, dataset_id):
    """
    Retrieve metadata about input dataset.

    """

    hit = es.get('datamart', '_doc', id=dataset_id)

    return hit


def get_joinable_datasets(es, lazo_client, data_profile, dataset_id=None,
                          query_args=None, tabular_variables=()):
    """
    Retrieve datasets that can be joined with an input dataset.

    :param es: Elasticsearch client.
    :param lazo_client: client for the Lazo Index Server
    :param data_profile: Profiled input dataset.
    :param dataset_id: The identifier of the desired DataMart dataset for augmentation.
    :param query_args: list of query arguments (optional).
    :param tabular_variables: specifies which columns to focus on for the search.
    """

    if not dataset_id and not data_profile:
        raise TypeError("Either a dataset id or a data profile "
                        "must be provided for the join")

    column_index_mapping = get_column_index_mapping(data_profile)

    # get the coverage for each column of the input dataset

    column_coverage = get_column_coverage(
        data_profile,
        column_index_mapping,
        tabular_variables
    )

    # search results
    search_results = list()

    # numerical, temporal, and spatial attributes
    for column in column_coverage:
        type_ = column_coverage[column]['type']
        type_value = column_coverage[column]['type_value']
        if type_ == 'spatial':
            spatial_results = get_spatial_join_search_results(
                es,
                column_coverage[column]['ranges'],
                dataset_id,
                query_args
            )
            for result in spatial_results:
                result['companion_column'] = column
                search_results.append(result)
        else:
            column_name = data_profile['columns'][int(column)]['name']
            numerical_results = get_numerical_join_search_results(
                es,
                type_,
                type_value,
                column_name,
                column_coverage[column]['ranges'],
                dataset_id,
                query_args
            )
            for result in numerical_results:
                result['companion_column'] = column
                search_results.append(result)

    # textual/categorical attributes
    lazo_sketches = get_lazo_sketches(
        data_profile,
        column_index_mapping,
        tabular_variables
    )
    for column in lazo_sketches:
        n_permutations, hash_values, cardinality = lazo_sketches[column]
        query_results = lazo_client.query_lazo_sketch_data(
            n_permutations,
            hash_values,
            cardinality
        )
        dataset_ids = list()
        column_names = list()
        scores = list()
        for dataset_id, column_name, threshold in query_results:
            dataset_ids.append(dataset_id)
            column_names.append(column_name)
            scores.append(threshold)
        textual_results = get_textual_join_search_results(
            es,
            dataset_ids,
            column_names,
            scores,
            query_args
        )
        for result in textual_results:
            result['companion_column'] = column
            search_results.append(result)

    search_results = sorted(
        search_results,
        key=lambda item: item['_score'],
        reverse=True
    )

    results = []
    for result in search_results:
        dt = result['_source']['dataset_id']
        info = get_dataset_metadata(es, dt)
        meta = info.pop('_source')
        # materialize = meta.get('materialize', {})
        if meta.get('description') and len(meta['description']) > 100:
            meta['description'] = meta['description'][:97] + "..."
        left_columns = []
        right_columns = []
        left_columns_names = []
        right_columns_names = []
        try:
            left_columns.append([int(result['companion_column'])])
            left_columns_names.append(
                [data_profile['columns'][int(result['companion_column'])]['name']]
            )
        except ValueError:
            index_1, index_2 = result['companion_column'].split(",")
            left_columns.append([int(index_1), int(index_2)])
            left_columns_names.append([data_profile['columns'][int(index_1)]['name'] +
                                       ', ' + data_profile['columns'][int(index_2)]['name']])
        if 'index' in result['_source']:
            right_columns.append([result['_source']['index']])
            right_columns_names.append([result['_source']['name']])
        else:
            right_columns.append([
                result['_source']['lat_index'],
                result['_source']['lon_index']])
            right_columns_names.append([
                result['_source']['lat'],
                result['_source']['lon']])
        results.append(dict(
            id=dt,
            score=result['_score'],
            # discoverer=materialize['identifier'],
            metadata=meta,
            augmentation={
                'type': 'join',
                'left_columns': left_columns,
                'right_columns': right_columns,
                'left_columns_names': left_columns_names,
                'right_columns_names': right_columns_names
            }
        ))

    return results


def get_column_information(data_profile, filter_=()):
    """
    Retrieve information about the columns (name and type) of a dataset.

    """

    output = dict()
    column_index = -1
    for column in data_profile['columns']:
        column_index += 1
        name = column['name']
        if filter_ and column_index not in filter_:
            continue
        # ignoring 'd3mIndex'
        if 'd3mIndex' in name:
            continue
        # ignoring phone numbers
        semantic_types = [
            sem for sem in column['semantic_types']
            if Type.PHONE_NUMBER not in sem
        ]
        for semantic_type in semantic_types:
            if semantic_type not in output:
                output[semantic_type] = []
            output[semantic_type].append(name)
        if not semantic_types:
            if column['structural_type'] not in output:
                output[column['structural_type']] = []
            output[column['structural_type']].append(name)
    return output


def get_unionable_datasets(es, data_profile, dataset_id=None,
                           query_args=None, tabular_variables=()):
    """
    Retrieve datasets that can be unioned to an input dataset using fuzzy search
    (max edit distance = 2).

    :param es: Elasticsearch client.
    :param data_profile: Profiled input dataset.
    :param dataset_id: The identifier of the desired DataMart dataset for augmentation.
    :param query_args: list of query arguments (optional).
    :param tabular_variables: specifies which columns to focus on for the search.
    """

    if not dataset_id and not data_profile:
        raise TypeError("Either a dataset id or a data profile "
                        "must be provided for the union")

    main_dataset_columns = get_column_information(
        data_profile=data_profile,
        filter_=tabular_variables
    )

    n_columns = 0
    for type_ in main_dataset_columns:
        n_columns += len(main_dataset_columns[type_])

    column_pairs = dict()
    for type_ in main_dataset_columns:
        for att in main_dataset_columns[type_]:
            partial_query = {
                'should': [
                    {
                        'term': {'columns.structural_type': type_}
                    },
                    {
                        'term': {'columns.semantic_types': type_}
                    },
                ],
                'must': [
                    {
                        'fuzzy': {'columns.name.raw': att}
                    }
                ],
                'minimum_should_match': 1
            }

            if dataset_id:
                partial_query['must'].append(
                    {'term': {'_id': dataset_id}}
                )

            query = {
                'nested': {
                    'path': 'columns',
                    'query': {
                        'bool': partial_query
                    },
                    'inner_hits': {'_source': False, 'size': 100}
                }
            }

            if not query_args:
                args = query
            else:
                args = [query] + query_args
            query_obj = {
                '_source': {
                    'excludes': [
                        'date',
                        'materialize',
                        'name',
                        'description',
                        'license',
                        'size',
                        'columns.mean',
                        'columns.stddev',
                        'columns.structural_type',
                        'columns.semantic_types'
                    ]
                },
                'query': {
                    'bool': {
                        'must': args,
                    }
                }
            }

            # logger.info("Query (union-fuzzy): %r", query_obj)

            from_ = 0
            result = es.search(
                index='datamart',
                body=query_obj,
                from_=from_,
                size=PAGINATION_SIZE,
                request_timeout=30
            )

            size = len(result['hits']['hits'])

            while size > 0:
                for hit in result['hits']['hits']:

                    dataset_name = hit['_id']
                    es_score = hit['_score'] if query_args else 1
                    columns = hit['_source']['columns']
                    inner_hits = hit['inner_hits']

                    if dataset_name not in column_pairs:
                        column_pairs[dataset_name] = []

                    for column_hit in inner_hits['columns']['hits']['hits']:
                        column_offset = int(column_hit['_nested']['offset'])
                        column_name = columns[column_offset]['name']
                        sim = compute_levenshtein_sim(att.lower(), column_name.lower())
                        column_pairs[dataset_name].append((att, column_name, sim, es_score))

                # pagination
                from_ += size
                result = es.search(
                    index='datamart',
                    body=query_obj,
                    from_=from_,
                    size=PAGINATION_SIZE,
                    request_timeout=30
                )
                size = len(result['hits']['hits'])

    scores = dict()
    for dataset in list(column_pairs.keys()):

        # choose pairs with higher similarity
        seen_1 = set()
        seen_2 = set()
        pairs = []
        for att_1, att_2, sim, es_score in sorted(column_pairs[dataset],
                                                  key=lambda item: item[2],
                                                  reverse=True):
            if att_1 in seen_1 or att_2 in seen_2:
                continue
            seen_1.add(att_1)
            seen_2.add(att_2)
            pairs.append((att_1, att_2, sim, es_score))

        if len(pairs) <= 1:
            del column_pairs[dataset]
            continue

        column_pairs[dataset] = pairs
        scores[dataset] = 0
        es_score = 0

        for i in range(len(column_pairs[dataset])):
            sim = column_pairs[dataset][i][2]
            scores[dataset] += sim
            es_score = max(es_score, column_pairs[dataset][i][3])

        scores[dataset] = (scores[dataset] / n_columns) * es_score

    sorted_datasets = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True
    )

    results = []
    for dt, score in sorted_datasets:
        info = get_dataset_metadata(es, dt)
        meta = info.pop('_source')
        # materialize = meta.get('materialize', {})
        if meta.get('description') and len(meta['description']) > 100:
            meta['description'] = meta['description'][:97] + "..."
        # TODO: augmentation information is incorrect
        left_columns = []
        right_columns = []
        left_columns_names = []
        right_columns_names = []
        for att_1, att_2, sim, es_score in column_pairs[dt]:
            if dataset_id:
                left_columns.append(
                    get_column_identifiers(es, [att_1], dataset_id=dataset_id)
                )
            else:
                left_columns.append(
                    get_column_identifiers(es, [att_1], data_profile=data_profile)
                )
            left_columns_names.append([att_1])
            right_columns.append(
                get_column_identifiers(es, [att_2], dataset_id=dt)
            )
            right_columns_names.append([att_2])
        results.append(dict(
            id=dt,
            score=score,
            # discoverer=materialize['identifier'],
            metadata=meta,
            augmentation={
                'type': 'union',
                'left_columns': left_columns,
                'right_columns': right_columns,
                'left_columns_names': left_columns_names,
                'right_columns_names': right_columns_names
            }
        ))

    return results
