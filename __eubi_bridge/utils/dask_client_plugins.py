# from watchdog.observers import Observer
import os, asyncio
import signal
# from watchdog.observers.polling import PollingObserver
# from watchdog.events import FileSystemEventHandler, FileSystemEvent
from distributed.diagnostics.plugin import SchedulerPlugin
from distributed.diagnostics.plugin import NannyPlugin
from distributed.diagnostics.plugin import SchedulerPlugin
from distributed import get_client, Client


class ShutdownOnWorkerRemovalPlugin(SchedulerPlugin):
    async def remove_worker(self, scheduler, worker, **kwargs):
        for worker in scheduler.workers.values():
            await scheduler.remove_worker(worker.address, stimulus_id="shutdown")
            print(f"Worker {worker} has been removed. Shutting down process.")
        await asyncio.sleep(5)
        await scheduler.close()
        # os.kill(os.getpid(), signal.SIGTERM)

class ForcefulShutdownPlugin(SchedulerPlugin):
    def start(self, scheduler):
        for idx, worker in dask_scheduler.workers.items():
            print(f"Worker shutting down: {idx}")
            scheduler.remove_worker(worker.address, stimulus_id="shutdown")



# class RecursiveWriteActivityHandler(FileSystemEventHandler):
#     def __init__(self):
#         self.last_save_moment = None
#     def on_created(self, event: FileSystemEvent) -> None:
#         self.last_save_moment = time.time()
#         self.event_details = [event.src_path, event.event_type]
#     # def on_modified(self, event: FileSystemEvent) -> None:
#     #     self.last_save_moment = time.time()
#     #     self.event_details = [event.src_path, event.event_type]
#     # def on_moved(self, event: FileSystemEvent) -> None:
#     #     self.last_save_moment = time.time()
#     #     self.event_details = [event.src_path, event.event_type]

# async def shutdown_callback(dask_scheduler,
#                             target,
#                             timeout = 60,
#                             verbose = False
#                             ):
#
#     observer = PollingObserver()
#     handler = RecursiveWriteActivityHandler()
#     observer.schedule(handler, path = target, recursive = True)
#
#     observer.start()
#     try:
#         while True:
#             await asyncio.sleep(5)
#             if handler.last_save_moment is None:
#                 # print(f"None")
#                 pass
#             else:
#                 if verbose:
#                     print(handler.event_details)
#                 interval = time.time() - handler.last_save_moment
#                 if  interval > timeout:
#                     print(f"{interval} seconds passed in the target folder without action.")
#                     print(f"Killing the workers.")
#                     for worker in dask_scheduler.workers.values():
#                         await dask_scheduler.remove_worker(worker.address, stimulus_id="shutdown")
#                     print(f"Closing the scheduler.")
#                     await dask_scheduler.close()
#                     observer.stop()
#                     break
#     finally:
#         observer.stop()
#         observer.join()


class ShutdownPlugin(SchedulerPlugin):
    def __init__(self, output_folder, timeout = 60, verbose = False):
        self.output_folder = output_folder
        self.timeout = timeout
        self.verbose = verbose

    def start(self, scheduler):
        asyncio.create_task(shutdown_callback(scheduler, self.output_folder, self.timeout, self.verbose))