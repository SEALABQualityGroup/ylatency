from pyspark.sql import functions as f, SparkSession, DataFrame
from datetime import datetime, timedelta, time
from functools import reduce


def __loadExperimentSpans(from_, to, spark):
    fromTimestamp = int(from_.timestamp() * 1000000)
    toTimestamp = int(to.timestamp() * 1000000)
    return (spark.read.format("es")
            .option("es.resource", "zipkin*")
            .load()
            .select('traceId',
                    'experiment')
            .filter(f.col('timestamp').between(fromTimestamp, toTimestamp))
            .filter(f.col('localEndpoint.serviceName') == 'web-service')
            .filter(f.col('kind') == 'SERVER'))


def loadSpansByInterval(from_, to, spark):
    fromTimestamp = int(from_.timestamp() * 1000000)
    toTimestamp = int(to.timestamp() * 1000000)
    return (spark.read.format("es")
            .option("es.resource", "zipkin*")
            .load()
            .select('traceId',
                    # f.concat_ws('_', *['localEndpoint.serviceName', 'name']).alias('endpoint'),
                    f.col('name').alias('endpoint'),
                    'duration',
                    'id',
                    'kind',
                    'timestamp',
                    'parentId')
            .filter(f.col('timestamp').between(fromTimestamp, toTimestamp)))


def loadSpansByDay(day, spark):
    return reduce(DataFrame.union,
                  [loadSpansByInterval(from_, to, spark) for from_, to in __HoursOfTheDay(day)])


class __HoursOfTheDay:
    def __init__(self, date):
        self.datetime = datetime.combine(date, time.min)
        self.hour = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.hour > 23:
            raise StopIteration
        else:
            from_ = self.datetime + timedelta(hours=self.hour)
            to = self.datetime + timedelta(hours=self.hour + 1) - timedelta(microseconds=1)
            self.hour += 1
            return from_, to


def __createEndpointTraces(spans):
    serverSpansWithClientsDuration = __createServerSpansWithClientsDuration(spans)
    serverSpansWithSelfDuration = (serverSpansWithClientsDuration
                                   .withColumn('self_duration',
                                               f.col('duration') - f.col('clients_duration')))
    avgDurPerTraceEndpoint = __createAvgDurPerTraceEndpointPairs(serverSpansWithSelfDuration)
    return (avgDurPerTraceEndpoint
            .groupBy('traceId')
            .pivot('endpoint')
            .agg(f.first('avg_self_duration').alias('avg_self_dur'),
                 f.first('avg_duration').alias('avg_dur'))
            .dropna())


def __createAvgDurPerTraceEndpointPairs(serverSpansWithSelfDuration):
    return (serverSpansWithSelfDuration
            .groupBy('traceId', 'endpoint')
            .agg(f.avg('duration').alias('avg_duration'),
                 f.avg('self_duration').alias('avg_self_duration')))


def __filterServerSpans(spans):
    return (spans.filter(spans.kind == 'SERVER')
            .drop('parentId', 'kind'))


def __filterClientSpans(spans):
    return (spans.filter(spans.kind == 'CLIENT')
            .drop('kind'))


def __createClientsDuration(clientSpans):
    return (clientSpans.groupBy('parentId')
            .agg(f.sum('duration').alias('clients_duration')))


def __createServerSpansWithClientsDuration(spans):
    serverSpans = __filterServerSpans(spans)
    clientSpans = __filterClientSpans(spans)
    clientsDuration = __createClientsDuration(clientSpans)
    return (serverSpans.join(clientsDuration,
                             serverSpans.id == clientsDuration.parentId,
                             'left_outer')
            .drop('parentId')
            .na.fill(0))


def __round_to_millis(traces):
    cols = [c for c in traces.columns if c != 'traceId' and c != 'experiment']
    return reduce(lambda df, c: df.withColumn(c, f.round(f.col(c) / 1000)),
                  cols,
                  traces)


def create_avg_traces(from_, to, spark):
    spans = loadSpansByInterval(from_, to, spark)
    traces_micros = __createEndpointTraces(spans)
    traces_millis = __round_to_millis(traces_micros)
    spans_exp = __loadExperimentSpans(from_, to, spark)

    return traces_millis.join(spans_exp, on='traceId')


def create_sum_traces(from_, to, spark):
    spans = loadSpansByInterval(from_, to, spark)
    traces_micros = (spans.filter(spans.kind == 'SERVER')
                          .groupBy('traceId', 'endpoint')
                          .agg(f.sum('duration').alias('duration'))
                          .groupby('traceId')
                          .pivot('endpoint')
                          .agg(f.first('duration')))
    traces_millis = __round_to_millis(traces_micros)
    spans_exp = __loadExperimentSpans(from_, to, spark)

    return traces_millis.join(spans_exp, on='traceId')
