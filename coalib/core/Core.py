import asyncio
import concurrent.futures
import functools
import logging
import multiprocessing

from coalib.core import DependencyTracker


def get_cpu_count():
    try:
        return multiprocessing.cpu_count()
    except NotImplementedError:  # pragma: no cover
        # cpu_count is not implemented for some CPU architectures/OSes
        return 1


def schedule_bears(bears,
                   result_callback,
                   dependency_tracker,
                   event_loop,
                   running_tasks,
                   executor):
    """
    Schedules the tasks of bears to the given executor and runs them on the
    given event loop.

    :param bears:
        A list of bear instances to be scheduled onto the process pool.
    :param result_callback:
        A callback function which is called when results are available.
    :param dependency_tracker:
        The object that keeps track of dependencies.
    :param event_loop:
        The asyncio event loop to schedule bear tasks on.
    :param running_tasks:
        Tasks that are already scheduled, organized in a dict with
        bear instances as keys and asyncio-coroutines as values containing
        their scheduled tasks.
    :param executor:
        The executor to which the bear tasks are scheduled.
    """
    for bear in bears:
        if bear in dependency_tracker.dependency_dict:
            logging.debug("Dependencies for '{}' not yet resolved. Holding "
                          "back.".format(bear.name))
        else:
            running_tasks[bear] = {
                event_loop.run_in_executor(
                    executor, bear.analyze, *bear_args, *bear_kwargs)
                for bear_args, bear_kwargs in bear.generate_tasks()}

            for task in running_tasks[bear]:
                task.add_done_callback(functools.partial(
                    finish_task, bear, result_callback, dependency_tracker,
                    running_tasks, event_loop, executor))

            logging.debug("Scheduled '{}' (tasks: {}).".format(
                bear.name, len(running_tasks[bear])))


def finish_task(bear,
                result_callback,
                dependency_tracker,
                running_tasks,
                event_loop,
                executor,
                task):
    """
    The callback for when a task of a bear completes. It is responsible for
    checking if the bear completed its execution and the handling of the
    result generated by the task. It also schedules new tasks if dependencies
    get resolved.

    :param bear:
        The bear that the task belongs to.
    :param result_callback:
        A callback function which is called when results are available.
    :param dependency_tracker:
        The object that keeps track of dependencies.
    :param running_tasks:
        Dictionary that keeps track of the remaining tasks of each bear.
    :param event_loop:
        The ``asyncio`` event loop bear-tasks are scheduled on.
    :param executor:
        The executor to which the bear tasks are scheduled.
    :param task:
        The task that completed.
    """
    # TODO Handle exceptions!!!

    # FIXME Long operations on the result-callback do block the scheduler
    # FIXME   significantly. It should be possible to schedule new Python
    # FIXME   Threads on the given event_loop and process the callback there.
    for result in task.result():
        # TODO Make a debug message?
        result_callback(result)

    running_tasks[bear].remove(task)
    if not running_tasks[bear]:
        resolved_bears = dependency_tracker.resolve(bear)

        if resolved_bears:
            schedule_bears(resolved_bears, result_callback, dependency_tracker,
                           event_loop, running_tasks, executor)

        del running_tasks[bear]

    if not running_tasks:
        event_loop.stop()


# TODO This is only relevant for instantiating bears.
def load_files(filenames):
    """
    Loads all files and arranges them inside a file-dictionary, where the keys
    are the filenames and the values the contents of the file (line-split
    including return characters).

    Files that fail to load are ignored.

    :param filenames: The names of the files to load.
    :return:          The file-dictionary.
    """
    file_dict = {}
    for filename in filenames:
        try:
            with open(filename, 'r', encoding='utf-8') as fl:
                file_dict[filename] = tuple(fl.readlines())
        except UnicodeDecodeError:
            logging.warning(
                "Failed to read file '{}'. It seems to contain non-unicode "
                'characters. Leaving it out.'.format(filename))
        except OSError as ex:  # pragma: no cover
            logging.warning(
                "Failed to read file '{}' because of an unknown error. "
                'Leaving it out.'.format(filename), exc_info=ex)

    logging.debug('Following files loaded:\n' + '\n'.join(file_dict.keys()))

    return file_dict


def run(bears, result_callback):
    """
    Runs a coala session.

    :param bears:
        The bear instances to run.
    :param result_callback:
        A callback function which is called when results are available. Must
        have following signature::

            def result_callback(result):
                pass
    """
    # TODO Maybe try to allow to exchange executor, especially to allow
    # TODO   distributed computation.

    # Set up event loop and executor.
    event_loop = asyncio.SelectorEventLoop()
    executor = concurrent.futures.ProcessPoolExecutor(
        max_workers=get_cpu_count())

    # Initialize dependency tracking.
    dependency_tracker = DependencyTracker()
    dependency_tracker.add_bear_dependencies(bears)

    # Let's go.
    schedule_bears(bears, result_callback, dependency_tracker, event_loop, {}, executor)
    try:
        event_loop.run_forever()
    finally:
        event_loop.close()
