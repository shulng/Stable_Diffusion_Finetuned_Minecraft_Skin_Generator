import accelerate
import diffusers
from diffusers import StableDiffusionXLPipeline
from scipy.spatial.distance import cdist
from PIL import Image
import os
import random
import numpy as np
import argparse
import logging
import torch
import sys

MODEL_NAME = "monadical-labs/minecraft-skin-generator-sdxl"
MASK_IMAGE = "images/half-transparency-mask.png"

SCALE = 12

IMAGE_WIDTH  = 768
IMAGE_HEIGHT = 768

# BACKGROUND_REGIONS is an array containing all of the areas that contain no pixels
# that are used in rendering the skin.  We'll use these areas to figure out the 
# color used to represent transparency in the skin.
BACKGROUND_REGIONS = [
    (32, 0, 40, 8),
    (56, 0, 64, 8)
]

# TRANSPARENT_REGIONS is an array containing all of the areas of the skin that need
# to have transparency restored.  Refer to: https://github.com/minotar/skin-spec for
# more information.
TRANSPARENT_REGIONS = [
        (40, 0, 48, 8),
        (48, 0, 56, 8),
        (32, 8, 40, 16),
        (40, 8, 48, 16),
        (48, 8, 56, 16),
        (56, 8, 64, 16)    
]

def get_background_color(image):
    '''
    Given a Minecraft skin image, loop over all of the regions considered to be the
    background, or ones that don't get rendered into a skin, and find the average
    color.  This color will be used when restoring transparency to the second layer.
    '''
    pixels = []
    
    # Loop over all the transparent regions, and create a list of the 
    # constituent pixels
    for region in BACKGROUND_REGIONS:
        swatch = image.crop(region)
        
        width, height = swatch.size
        np_swatch = np.array(swatch)

        # Reshape so that we have an list of pixel arrays.
        np_swatch = np_swatch.reshape(width * height, 3)

        if len(pixels) == 0:
            pixels = np_swatch
        else:
            np.concatenate((pixels, np_swatch))

    # Get the mean RGB values for the pixels in the background regions.
    (r, g, b) = np.mean(np_swatch, axis=0, dtype=int)
       
    return [(r, g, b)]

def restore_region_transparency(image, region, transparency_color, cutoff=50):
    changed = 0
    # Loop over all the pixels in the region we're processing.
    for x in range(region[0], region[2]):
        for y in range(region[1], region[3]):
            pixel = [image.getpixel((x, y))]
            pixel = [(pixel[0][0], pixel[0][1], pixel[0][2])]
          
            # Calculate the Cartesian distance between the current pixel and the
            # transparency color.
            dist  = cdist(pixel, transparency_color)
           
            # If the distance is less than or equal to the cutoff, then set the
            # pixel as transparent.
            if dist <= cutoff:
                image.putpixel((x, y), (255, 255, 255, 0))
                changed = changed + 1

    return image, changed

def restore_skin_transparency(image, transparency_color, cutoff=50):
    # Convert the generated RGB image back to RGBA to restore transparency.
    image = image.convert("RGBA")

    total_changed = 0
    # Restore transparency in each region.
    for region in TRANSPARENT_REGIONS:
        image, changed = restore_region_transparency(image, region, transparency_color, cutoff=cutoff)
        total_changed = total_changed + changed
        
    return image, total_changed

def extract_minecraft_skin(generated_image, cutoff=50):
    # Crop out the skin portion from the  generated file.
    image = generated_image.crop((0, 0, IMAGE_WIDTH, int(IMAGE_HEIGHT/2)))

    # Scale the image down to the 64x32 size.
    skin = image.resize((64, 32), Image.NEAREST)

    # Get the average background transparency color from the skin.  We'll use this
    # later when we need to determine which cluster corresponds to the background
    # pixels.
    color = get_background_color(skin)

    # Restore the transparent parts in the skin background.
    transparent_skin, _ = restore_skin_transparency(skin, color, cutoff=cutoff)
    
    # Convert the bits of the background that aren't involved with transparency
    # to all white.
    mask = Image.open(MASK_IMAGE)
    transparent_skin.alpha_composite(mask)

    return transparent_skin

def main(prompt, num_inference_steps, guidance_scale, model_precision_type, seed, filename, logger):
    # Enable GPU acceleration frameworks, if enabled.

    if model_precision_type == "fp16":
        dtype = torch.float16
    elif model_precision_type == "fp32":
        dtype = torch.float32

    if torch.cuda.is_available() and torch.backends.cuda.is_built():
        # A CUDA compatible GPU was found.
        device = "cuda"
        print("CUDA device found, enabling.")
    elif torch.backends.mps.is_available() and torch.backends.mps.is_built():
        # Apple M1/M2 machines have the MPS framework.
        device = "mps"
        print("Apple MPS device found, enabling.")
    else:
        # Else we're defaulting to CPU.
        device = "cpu"
        print("No CUDA or MPS devices found, running on CPU.")

    # Load (and possibly download) our Minecraft model.
    logger.info("Loading HuggingFace model: '{}'.".format(MODEL_NAME))
    if device == "cpu":
        pipeline = StableDiffusionXLPipeline.from_pretrained(MODEL_NAME)
    else:
        pipeline = StableDiffusionXLPipeline.from_pretrained(MODEL_NAME, torch_dtype=dtype)
    pipeline.to(device)


    # random option for 0 seed
    if seed == 0:
        seed = random.randint(1, 100000)
    else:
        seed = seed


    # Generate the image given the prompt provided on the command line.
    logger.info("Generating skin with prompt: '{}'.".format(prompt))
    generated_image = pipeline(
        prompt=prompt,
        num_inference_steps=num_inference_steps,
        height=768,
        width=768,
        guidance_scale=guidance_scale,
        num_images_per_prompt=1,
        seed=seed
    ).images[0]

    # Extract and scale down the Minecraft skin portion of the image.
    logger.info("Extracting and scaling Minecraft skin from generated image.")
    minecraft_skin = extract_minecraft_skin(generated_image)

    logger.info("Saving skin to: '{}'.".format(filename))
    os.chdir("output_minecraft_skins")
    minecraft_skin.save(filename)
    os.chdir("..")
    
if __name__ == "__main__":
    logging.basicConfig(stream=sys.stdout, level=logging.ERROR, format='[%(asctime)s] %(levelname)s - %(message)s')

    logger = logging.getLogger("minecraft-skins")

    # Get all of the command line parameters and options passed to us.
    parser = argparse.ArgumentParser(description='Process the command line arguments.')

    parser.add_argument('prompt', type=str, help='Stable Diffusion prompt to be used to generate skin')
    parser.add_argument('num_inference_steps', type=int, help='The number of denoising steps of the image. More denoising steps usually lead to a higher quality image at the cost of slower inference')
    parser.add_argument('guidance_scale', type=float, help='How closely the generated image adheres to the prompt')
    parser.add_argument('model_precision_type', type=str, help='The precision type to load the model, like fp16 which is faster, or fp32 which gives better results')
    parser.add_argument('seed', type=int, help='A starting point to initiate the generation process')
    parser.add_argument('filename', type=str, help='Name of the output generated Minecraft skin file')
    parser.add_argument('--model_3d', help='Show the output as a 3D Model too', action='store_true', default=False)
    parser.add_argument('--verbose', help='Produce verbose output while running', action='store_true', default=False)

    args = parser.parse_args()

    filename = args.filename
    verbose = args.verbose
    prompt = args.prompt
    num_inference_steps = args.num_inference_steps
    guidance_scale = args.guidance_scale
    model_precision_type = args.model_precision_type
    seed = args.seed
    model_3d = args.model_3d
    
    if verbose:
        logger.setLevel(logging.INFO)

    main(prompt, num_inference_steps, guidance_scale, model_precision_type, seed, filename, logger)

    if model_3d:
        os.chdir("Scripts")
        command_3d_model = f"python to_3d_model.py '{filename}'"
        os.system(command_3d_model)
        os.chdir("..")

    print("Successfully generated the skin!")
