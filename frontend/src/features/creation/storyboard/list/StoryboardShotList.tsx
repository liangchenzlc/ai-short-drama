import { StoryboardShotCard } from '@host/features/creation/storyboard/list/StoryboardShotCard';
import { LazyLoadMore } from '@host/shared/ui/LazyLoadMore';
import type { StoryboardShotListProps } from '@host/types/creation/storyboard/list/views';
import { Button } from 'antd';

export function StoryboardShotList({
  loading,
  page,
  loadError,
  readOnly,
  busy,
  add,
  visibleShots,
  setSearch,
  setPendingOnly,
  mediaTab,
  selectedShotId,
  selectionDisabled,
  batchSelection,
  batchMode,
  selectShot,
  deleteShot,
  deletingShotId,
  lockedShots,
  moreLoading,
  moreError,
  loadMoreShots
}: StoryboardShotListProps) {
  return (<div className="storyboard-list lazy-scroll" aria-busy={loading}>
    {loading && !page ? (
      Array.from({ length: 3 }, (_, index) => (
        <div
          className="storyboard-card-skeleton"
          key={index}
          role={index === 0 ? 'status' : undefined}
          aria-label={index === 0 ? '正在加载分镜' : undefined}
        >
          <span />
          <div>
            <i />
            <i />
          </div>
        </div>
      ))
    ) : (
      <>
        {page && !page.items.length && !loadError && (
          <div className="studio-empty">
            <p>还没有镜头</p>
            <Button disabled={readOnly || busy || loading} onClick={() => void add()}>
              新增第一个分镜
            </Button>
          </div>
        )}
        {!!page?.items.length && !visibleShots.length && !loadError && (
          <div className="studio-empty storyboard-filter-empty" role="status">
            <p>已加载镜头中没有匹配项</p>
            <Button
              type="text"
              onClick={() => {
                setSearch('');
                setPendingOnly(false);
              }}
            >
              清空筛选
            </Button>
          </div>
        )}
        {visibleShots.map((shot) => (
          <StoryboardShotCard
            key={shot.id}
            shot={shot}
            media={mediaTab === 'video' ? 'video' : 'image'}
            selected={selectedShotId === shot.id}
            disabled={selectionDisabled}
            batch={batchSelection.enabled && batchMode && !readOnly}
            checked={batchSelection.ids.includes(shot.id)}
            onSelect={() => void selectShot(shot.id)}
            onCheck={(checked) => batchSelection.toggle(shot.id, checked)}
            onDelete={readOnly ? undefined : () => void deleteShot(shot.id)}
            deleting={deletingShotId === shot.id}
            deleteDisabled={selectionDisabled || busy || lockedShots.size > 0}
          />
        ))}
        <LazyLoadMore
          hasMore={!!page && page.items.length < page.total}
          loading={loading || moreLoading}
          error={moreError}
          onLoad={() => void loadMoreShots()}
        />
      </>
    )}
  </div>);
}
