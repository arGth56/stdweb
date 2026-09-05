import os
import pickle
import shutil
from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser, FormParser
from celery import chain
import numpy as np
from astropy.table import Table

from .models import Task, Preset
from .serializers import TaskUploadSerializer, TaskSerializer, PresetSerializer
from .views import handle_uploaded_file
from . import celery_tasks
from django.http import HttpResponse
import csv


class TaskUploadAPIView(APIView):
    """API endpoint for uploading FITS files and creating tasks"""
    
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def post(self, request, format=None):
        serializer = TaskUploadSerializer(data=request.data)
        
        if serializer.is_valid():
            # Extract validated data
            file = serializer.validated_data['file']
            title = serializer.validated_data.get('title', '')
            preset_id = serializer.validated_data.get('preset')
            do_inspect = serializer.validated_data.get('do_inspect', False)
            do_photometry = serializer.validated_data.get('do_photometry', False)
            do_simple_transients = serializer.validated_data.get('do_simple_transients', False)
            do_subtraction = serializer.validated_data.get('do_subtraction', False)
            
            # Create task
            task = Task(
                title=title,
                original_name=file.name,
                user=request.user
            )
            task.save()  # Save to get task.id
            
            # Handle file upload
            try:
                handle_uploaded_file(file, os.path.join(task.path(), 'image.fits'))
            except Exception as e:
                task.delete()  # Clean up if file upload fails
                return Response(
                    {'error': f'File upload failed: {str(e)}'}, 
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )
            
            # Apply config preset if provided
            if preset_id:
                try:
                    preset = Preset.objects.get(id=preset_id)
                    task.config.update(preset.config)
                    
                    # Copy preset files if they exist
                    if preset.files:
                        for filename in preset.files.split('\n'):
                            if filename.strip():
                                try:
                                    shutil.copy(filename.strip(), task.path())
                                except Exception as e:
                                    # Log error but don't fail the upload
                                    pass
                except Preset.DoesNotExist:
                    return Response(
                        {'error': 'Preset not found'}, 
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Extract and set all configuration parameters
            config_params = [
                # Photometry parameters
                'sn', 'initial_aper', 'initial_r0', 'bg_size', 'minarea',
                'rel_aper', 'rel_bg1', 'rel_bg2', 'fwhm_override',
                'filter', 'cat_name', 'cat_limit', 'spatial_order', 'use_color', 'sr_override',
                'prefilter_detections', 'filter_blends', 'diagnose_color', 'refine_wcs',
                'blind_match_wcs', 'inspect_bg', 'centroid_targets', 'nonlin',
                'blind_match_ps_lo', 'blind_match_ps_up', 'blind_match_center', 'blind_match_sr0',
                'filter_vizier', 'filter_skybot', 'filter_prefilter',
                # Inspection parameters
                'target', 'gain', 'saturation', 'time',
                # Template selection
                'template', 'template_filter'
            ]
            
            for param in config_params:
                value = serializer.validated_data.get(param)
                if value is not None:
                    task.config[param] = value
            
            task.state = 'uploaded'
            task.save()
            
            # Initiate processing steps if requested
            todo = []
            
            if do_inspect:
                todo.extend([
                    celery_tasks.task_set_state.subtask(args=[task.id, 'inspect'], immutable=True),
                    celery_tasks.task_inspect.subtask(args=[task.id, False], immutable=True),
                    celery_tasks.task_break_if_failed.subtask(args=[task.id], immutable=True),
                    celery_tasks.task_set_state.subtask(args=[task.id, 'inspect_done'], immutable=True)
                ])
            
            if do_photometry:
                todo.extend([
                    celery_tasks.task_set_state.subtask(args=[task.id, 'photometry'], immutable=True),
                    celery_tasks.task_photometry.subtask(args=[task.id, False], immutable=True),
                    celery_tasks.task_break_if_failed.subtask(args=[task.id], immutable=True),
                    celery_tasks.task_set_state.subtask(args=[task.id, 'photometry_done'], immutable=True)
                ])
            
            if do_simple_transients:
                todo.extend([
                    celery_tasks.task_set_state.subtask(args=[task.id, 'transients_simple'], immutable=True),
                    celery_tasks.task_transients_simple.subtask(args=[task.id, False], immutable=True),
                    celery_tasks.task_break_if_failed.subtask(args=[task.id], immutable=True),
                    celery_tasks.task_set_state.subtask(args=[task.id, 'transients_simple_done'], immutable=True)
                ])
            
            if do_subtraction:
                todo.extend([
                    celery_tasks.task_set_state.subtask(args=[task.id, 'subtraction'], immutable=True),
                    celery_tasks.task_subtraction.subtask(args=[task.id, False], immutable=True),
                    celery_tasks.task_break_if_failed.subtask(args=[task.id], immutable=True),
                    celery_tasks.task_set_state.subtask(args=[task.id, 'subtraction_done'], immutable=True)
                ])
            
            if todo:
                todo.append(celery_tasks.task_finalize.subtask(args=[task.id], immutable=True))
                task.celery_id = chain(todo).apply_async()
                task.state = 'running'
                task.save()
            
            # Return task data
            task_serializer = TaskSerializer(task)
            return Response(
                {
                    'message': 'File uploaded successfully',
                    'task': task_serializer.data
                }, 
                status=status.HTTP_201_CREATED
            )
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def task_detail_api(request, task_id):
    """Get task details by ID"""
    try:
        task = Task.objects.get(id=task_id, user=request.user)
        serializer = TaskSerializer(task)
        return Response(serializer.data)
    except Task.DoesNotExist:
        return Response(
            {'error': 'Task not found'}, 
            status=status.HTTP_404_NOT_FOUND
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def task_list_api(request):
    """List user's tasks"""
    tasks = Task.objects.filter(user=request.user).order_by('-created')
    serializer = TaskSerializer(tasks, many=True)
    return Response(serializer.data)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def preset_list_api(request):
    """List available presets"""
    presets = Preset.objects.all()
    serializer = PresetSerializer(presets, many=True)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def task_action_api(request, task_id):
    """Trigger processing actions on existing tasks"""
    try:
        task = Task.objects.get(id=task_id, user=request.user)
        
        # Check if task is already running
        if task.celery_id is not None:
            # Check if the task is actually running
            from . import celery
            ctask = celery.app.AsyncResult(task.celery_id)
            if ctask.state not in ['REVOKED', 'FAILURE', 'SUCCESS']:
                return Response(
                    {'error': f'Task {task_id} is already running'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        action = request.data.get('action')
        if not action:
            return Response(
                {'error': 'Action parameter is required'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Import celery tasks
        from . import celery_tasks

        # --- NEW: update configuration if extra parameters are supplied ---
        # Allow the same subset we accept on upload so that users can tweak
        # parameters like gain/saturation before re-running an action.
        extra_config_params = [
            # Photometry parameters
            'sn', 'initial_aper', 'initial_r0', 'bg_size', 'minarea',
            'rel_aper', 'rel_bg1', 'rel_bg2', 'fwhm_override',
            'filter', 'cat_name', 'cat_limit', 'spatial_order', 'use_color', 'sr_override',
            'prefilter_detections', 'filter_blends', 'diagnose_color', 'refine_wcs',
            'blind_match_wcs', 'inspect_bg', 'centroid_targets', 'nonlin',
            'blind_match_ps_lo', 'blind_match_ps_up', 'blind_match_center', 'blind_match_sr0',
            'filter_vizier', 'filter_skybot', 'filter_prefilter',
            # Inspection parameters
            'target', 'gain', 'saturation', 'time',
            # Template selection
            'template', 'template_catalog', 'template_filter'
        ]

        updated = False
        for param in extra_config_params:
            if param in request.data:
                # DRF gives QueryDict, convert e.g. "true"/"false" to bool where possible
                value = request.data.get(param)
                if isinstance(value, str):
                    if value.lower() == 'true':
                        value = True
                    elif value.lower() == 'false':
                        value = False
                task.config[param] = value
                updated = True
        if updated:
            task.save()
        # --- END NEW CODE ---

        # Handle different actions
        if action == 'inspect':
            task.celery_id = celery_tasks.task_inspect.delay(task.id).id
            task.state = 'inspect'
            task.save()
            return Response({'message': f'Started inspection for task {task_id}'})
            
        elif action == 'photometry':
            task.celery_id = celery_tasks.task_photometry.delay(task.id).id
            task.state = 'photometry'
            task.save()
            return Response({'message': f'Started photometry for task {task_id}'})
            
        elif action == 'transients_simple':
            task.celery_id = celery_tasks.task_transients_simple.delay(task.id).id
            task.state = 'transients_simple'
            task.save()
            return Response({'message': f'Started simple transient detection for task {task_id}'})
            
        elif action == 'subtraction':
            task.celery_id = celery_tasks.task_subtraction.delay(task.id).id
            task.state = 'subtraction'
            task.save()
            return Response({'message': f'Started subtraction for task {task_id}'})
            
        elif action == 'cleanup':
            task.celery_id = celery_tasks.task_cleanup.delay(task.id).id
            task.state = 'cleanup'
            task.save()
            return Response({'message': f'Started cleanup for task {task_id}'})
            
        else:
            return Response(
                {'error': f'Unknown action: {action}. Valid actions are: inspect, photometry, transients_simple, subtraction, cleanup'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
            
    except Task.DoesNotExist:
        return Response(
            {'error': 'Task not found'}, 
            status=status.HTTP_404_NOT_FOUND
        ) 


# ---------------------------------------------------------------------------
# Endpoint: upload custom template FITS file
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def task_upload_template_api(request, task_id):
    """Upload a custom template FITS file as custom_template.fits in the task dir."""
    if 'template_file' not in request.FILES:
        return Response({'error': 'No file provided (use form field "template_file")'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        task = Task.objects.get(id=task_id, user=request.user)
    except Task.DoesNotExist:
        return Response({'error': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)

    try:
        handle_uploaded_file(request.FILES['template_file'],
                             os.path.join(task.path(), 'custom_template.fits'))
    except Exception as exc:
        return Response({'error': f'Upload failed: {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response({'message': 'Custom template uploaded as custom_template.fits'}) 


# ---------------------------------------------------------------------------
# Endpoint: export tasks as CSV (email, created date, original image name)
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def task_export_csv_api(request):
    """Return a CSV of tasks with columns: email, created, original_name.

    - If the requesting user is staff, include all tasks.
    - Otherwise, include only tasks belonging to the user.
    """
    if request.user.is_staff:
        queryset = Task.objects.all().order_by('-created')
    else:
        queryset = Task.objects.filter(user=request.user).order_by('-created')

    # Prepare CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="tasks_export.csv"'

    writer = csv.writer(response)
    writer.writerow(['email', 'username', 'created', 'original_name'])

    for task in queryset.select_related('user').only('original_name', 'created', 'user__email', 'user__username'):
        username = task.user.username if task.user and task.user.username else ''
        email = ''
        if task.user:
            if getattr(task.user, 'email', None):
                email = task.user.email
            elif username and '@' in username:
                # Fallback: if username looks like an email, use it
                email = username
        writer.writerow([email, username, task.created.isoformat(), task.original_name])

    return response


# ---------------------------------------------------------------------------
# Endpoint: export comparison stars with instrumental mags & astrometry
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([])
def task_comparison_stars_csv(request, task_id):
    """Export the catalogue of matched comparison stars for a task.

    Each row is a star used (or considered) in the photometric calibration,
    with its astrometric position, catalog magnitudes, instrumental
    magnitude, zero-point residual, and whether it was kept in the final
    fit.  This allows inter-observer comparison of calibration quality.

    Query params:
        ?good_only=1   — only export stars kept in the final fit
    """
    try:
        task = Task.objects.get(id=task_id)
    except Task.DoesNotExist:
        return Response({'error': 'Task not found'},
                        status=status.HTTP_404_NOT_FOUND)

    basepath = task.path()

    phot_path = os.path.join(basepath, 'photometry.pickle')
    obj_path = os.path.join(basepath, 'objects.vot')
    cat_path = os.path.join(basepath, 'cat.vot')

    for fpath, label in [(phot_path, 'photometry.pickle'),
                         (obj_path, 'objects.vot'),
                         (cat_path, 'cat.vot')]:
        if not os.path.exists(fpath):
            return Response(
                {'error': f'{label} not found — run photometry first'},
                status=status.HTTP_404_NOT_FOUND)

    with open(phot_path, 'rb') as f:
        m = pickle.load(f)

    obj = Table.read(obj_path)
    cat = Table.read(cat_path)

    oidx = m['oidx']
    cidx = m['cidx']
    idx = m.get('idx', np.ones(len(oidx), dtype=bool))

    good_only = request.GET.get('good_only', '').lower() in ('1', 'true', 'yes')

    cat_mag_col = m.get('cat_col_mag', '')
    cat_color1 = m.get('cat_col_mag1', '')
    cat_color2 = m.get('cat_col_mag2', '')
    color_term = m.get('color_term')

    gaia_cols = [c for c in cat.colnames
                 if c.lower() in ('gmag', 'bpmag', 'rpmag',
                                  'e_gmag', 'e_bpmag', 'e_rpmag')]
    photo_cols = [c for c in cat.colnames
                  if c.lower().endswith('mag') and c not in gaia_cols]

    cfg = task.config or {}
    target = cfg.get('target', '').strip().replace(' ', '_')
    filt = cfg.get('filter', '')
    obs_time = cfg.get('time', '')
    obs_date = obs_time[:10] if obs_time else task.created.strftime('%Y-%m-%d')

    exptime = ''
    fits_path = os.path.join(basepath, 'image.fits')
    if os.path.exists(fits_path):
        try:
            from astropy.io import fits as pyfits
            exptime = pyfits.getheader(fits_path, -1).get('EXPTIME', '')
        except Exception:
            pass
    exp_str = f'_{int(float(exptime))}s' if exptime else ''

    parts = [p for p in [target, filt, exp_str.lstrip('_'), obs_date] if p]
    slug = '_'.join(parts) if parts else f'task_{task_id}'

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = (
        f'attachment; filename="{slug}_comparison_stars.csv"')

    writer = csv.writer(response)

    header = [
        'ra', 'dec',
        'x', 'y',
        'inst_mag', 'inst_mag_err',
        'cat_mag', 'cat_mag_err',
        'zero_point', 'zero_point_model', 'residual',
        'color',
        'used_in_fit',
        'flags',
        'fwhm',
    ]
    header += gaia_cols
    header += photo_cols

    meta_row = [
        f'# task_id={task_id}',
        f'cat_mag_column={cat_mag_col}',
        f'color={cat_color1}-{cat_color2}' if cat_color1 else '',
        f'color_term={color_term}',
        f'intrinsic_rms={m.get("intrinsic_rms", "")}',
        f'n_matched={len(oidx)}',
        f'n_used={int(np.sum(idx))}',
    ]
    writer.writerow(meta_row)
    writer.writerow(header)

    for k in range(len(oidx)):
        if good_only and not idx[k]:
            continue

        oi = oidx[k]
        ci = cidx[k]
        o = obj[oi]
        c = cat[ci]

        zp = float(m['zero'][k]) if 'zero' in m else ''
        zp_model = float(m['zero_model'][k]) if 'zero_model' in m else ''
        resid = float(zp - zp_model) if zp != '' and zp_model != '' else ''

        omag = float(m['omag'][k]) if 'omag' in m else ''
        omag_err = float(m['omag_err'][k]) if 'omag_err' in m else ''
        cmag = float(m['cmag'][k]) if 'cmag' in m else ''
        cmag_err = float(m['cmag_err'][k]) if 'cmag_err' in m else ''
        color = float(m['color'][k]) if 'color' in m and np.isfinite(m['color'][k]) else ''

        row = [
            f"{float(o['ra']):.7f}",
            f"{float(o['dec']):.7f}",
            f"{float(o['x']):.2f}",
            f"{float(o['y']):.2f}",
            f"{omag:.4f}" if omag != '' else '',
            f"{omag_err:.4f}" if omag_err != '' else '',
            f"{cmag:.4f}" if cmag != '' else '',
            f"{cmag_err:.4f}" if cmag_err != '' else '',
            f"{zp:.4f}" if zp != '' else '',
            f"{zp_model:.4f}" if zp_model != '' else '',
            f"{resid:.4f}" if resid != '' else '',
            f"{color:.4f}" if color != '' else '',
            '1' if idx[k] else '0',
            int(o['flags']),
            f"{float(o['fwhm']):.2f}" if 'fwhm' in o.colnames else '',
        ]

        for gc in gaia_cols:
            val = c[gc]
            row.append(f"{float(val):.4f}" if np.isfinite(val) else '')
        for pc in photo_cols:
            val = c[pc]
            row.append(f"{float(val):.4f}" if np.isfinite(val) else '')

        writer.writerow(row)

    return response