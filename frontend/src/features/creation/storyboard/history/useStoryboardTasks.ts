import { generations } from '@host/api/generations/tasks/requests';
import { errorMessage } from '@host/api/http';
import { shouldPollStoryboardTasks } from '@host/features/creation/storyboard/sessions/storyboard-session';
import type { GenerationSummary } from '@host/types/generations/api';
import { useEffect, useState } from 'react';

export function useStoryboardTasks(projectId: string, scriptId: string | null) {
  const [tasks, setTasks] = useState<GenerationSummary[]>([]);
  const [tasksTotal, setTasksTotal] = useState(0);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasksLoaded, setTasksLoaded] = useState(false);
  const [tasksError, setTasksError] = useState('');
  const [taskRevision, setTaskRevision] = useState(0);
  const generationPending = shouldPollStoryboardTasks(tasks);
  useEffect(() => {
    const controller = new AbortController();
    setTasksLoading(true);
    setTasksError('');
    generations
      .list(
        {
          service_type: 'text',
          project_id: projectId,
          source_scene: 'script_shots',
          offset: 0,
          limit: 20,
        },
        controller.signal,
      )
      .then((page) => {
        if (!controller.signal.aborted) {
          setTasks((current) => [
            ...page.items,
            ...current.filter((task) => !page.items.some((next) => next.generation_id === task.generation_id)),
          ]);
          setTasksTotal(page.total);
          setTasksLoaded(true);
        }
      })
      .catch((cause) => {
        if (!controller.signal.aborted) setTasksError(errorMessage(cause));
      })
      .finally(() => {
        if (!controller.signal.aborted) setTasksLoading(false);
      });
    return () => controller.abort();
  }, [projectId, scriptId, taskRevision]);
  useEffect(() => {
    if (!generationPending) return;
    const timer = setTimeout(() => setTaskRevision((revision) => revision + 1), 3000);
    return () => clearTimeout(timer);
  }, [generationPending, tasks]);
  return {
    tasks,
    setTasks,
    tasksTotal,
    setTasksTotal,
    tasksLoading,
    setTasksLoading,
    tasksLoaded,
    setTasksLoaded,
    tasksError,
    setTasksError,
    setTaskRevision,
  };
}
